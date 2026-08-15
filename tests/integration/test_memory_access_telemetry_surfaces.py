"""Every real surface that can read curated memory, classified by MEASUREMENT.

Each surface below is invoked for real -- ``bin/gaia`` as a subprocess for the
CLI ones, the function itself for context assembly -- against a seeded scratch
database, and its verdict is checked against the counter deltas that landed in
that database. Nothing here mocks ``record_memory_access`` or asserts on a call
count: a surface is injection, deliberate, kernel, or neither because of what
the DB looks like afterwards.

v50 (usar-la-telemetria-de-memoria-edad-sesgo-y-pesaje, task 4) splits a third
axis, ``kernel``, off ``injection``: the dispatch kernel's own "How the user
works" block (rendered by ``build_memory_block``, fired on EVERY subagent
dispatch over a fixed ``type=user AND audience=executor`` row set) used to
share ``injection``'s columns and dominated that axis by construction. It has
its own recipe (``test_kernel_memory_block_counts_the_rows_it_renders``) and
its own dedicated cross-axis proof
(``test_kernel_dispatch_and_context_digest_move_disjoint_axes_on_the_same_row``),
rather than a ``SURFACES`` entry, because no CLI subprocess triggers it -- it
is context assembly, invoked the same way ``test_kernel_memory_block_counts_
the_rows_it_renders`` already invokes it.

Three guards keep the census from going stale as a hand-written list would:

* ``test_every_memory_action_is_classified`` reads the action set off the
  argument parser, so a new ``gaia memory`` subcommand fails until a recipe
  classifies it.
* ``test_every_flag_is_exercised_or_declared_non_classifying`` does the same at
  flag level -- two of the three known defects in this family were flag-level
  (``show --links``, ``--initiative`` in text), never subcommand-level.
* ``test_every_bump_call_site_belongs_to_a_classified_surface`` scans the
  source for ``record_memory_access`` call sites, so a bump wired into a
  module no recipe covers fails too.

Each recipe also declares a fragment its output must contain. A surface that
silently stopped working would move no counter and would otherwise pass its
"neither" verdict vacuously -- which is exactly how `gaia query` dumping every
row while counting none has to be proven, rather than asserted.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BIN_DIR = _REPO_ROOT / "bin"
for _path in (str(_REPO_ROOT), str(_BIN_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

WORKSPACE = "me"
_GAIA = _REPO_ROOT / "bin" / "gaia"

INJECTION = "injection"
DELIBERATE = "deliberate"
KERNEL = "kernel"

#: Expected 3-tuple delta (injection, deliberate, kernel) per verdict. A
#: surface that moves rows carries no ``KERNEL`` entry today -- nothing in
#: ``SURFACES`` reaches the kernel block, which is a context-assembly
#: function, not a CLI subcommand -- but the mapping is total over the three
#: kinds so a future kernel-classified surface needs no new branch here.
_EXPECTED_DELTA: dict[str, tuple[int, int, int]] = {
    INJECTION: (1, 0, 0),
    DELIBERATE: (0, 1, 0),
    KERNEL: (0, 0, 1),
}


# ---------------------------------------------------------------------------
# Seeded corpus
# ---------------------------------------------------------------------------
#
# Shaped so each selection resolves to a pinned row set rather than a sample:
# the two live threads sit in DIFFERENT initiatives, so the digest (one bullet
# per initiative) renders both and neither can shadow the other.

@dataclass(frozen=True)
class Seed:
    name: str
    type: str
    class_: str | None = None
    status: str | None = None
    initiative: str | None = None
    audience: str | None = None


SEEDS: tuple[Seed, ...] = (
    Seed("t_alpha", "project", class_="thread", status="open", initiative="alpha"),
    Seed("t_beta", "project", class_="thread", status="carry_forward",
         initiative="beta"),
    # type=user: the anchor section carries the user's instructions, so a
    # type=project anchor sharing the workspace no longer reaches it.
    Seed("a_anchor", "user", class_="anchor"),
    Seed("u_exec", "user", audience="executor"),
    # class=anchor (not the default "log") so get-relevant's anchor section
    # -- which pins type=user rows to the top -- can select it too: the row
    # both the kernel block AND a session-context digest can reach, used by
    # test_kernel_dispatch_and_context_digest_move_disjoint_axes_on_the_same_row.
    Seed("u_kernel_and_digest", "user", class_="anchor", audience="executor"),
    Seed("atom_seeded", "atom"),
    Seed("w_edit", "project"),
    Seed("w_append", "project"),
    Seed("w_reclass", "project"),
    Seed("w_link_src", "project"),
    Seed("w_link_dst", "project"),
    Seed("w_delete", "project"),
)

EPISODE_ID = "ep_seeded_for_surface_census"


# ---------------------------------------------------------------------------
# Surface recipes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Surface:
    """One invocable surface plus the counter movement it owes."""

    surface_id: str
    argv: tuple[str, ...]
    kind: str | None
    rows: frozenset[str] = frozenset()
    action: str | None = None
    returncode: int = 0
    output_matches: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.kind is None) != (not self.rows):
            raise ValueError(
                f"{self.surface_id}: a kind and a non-empty row set imply "
                f"each other"
            )


def _read(surface_id, argv, kind, rows=(), *, action, rc=0, contains=()):
    return Surface(
        surface_id=surface_id, argv=tuple(argv), kind=kind,
        rows=frozenset(rows), action=action, returncode=rc,
        output_matches=tuple(contains),
    )


SURFACES: tuple[Surface, ...] = (
    # -- gaia memory: windows over the table -------------------------------
    _read("search-text", ["memory", "search", "seeded", "--workspace", WORKSPACE],
          None, action="search", contains=("t_alpha",)),
    _read("search-json",
          ["memory", "search", "seeded", "--workspace", WORKSPACE,
           "--scope", "memory", "--json", "--limit", "50"],
          None, action="search", contains=("t_alpha",)),
    _read("list-table", ["memory", "list", "--workspace", WORKSPACE],
          None, action="list", contains=("t_alpha", "u_exec")),
    _read("list-json",
          ["memory", "list", "--workspace", WORKSPACE, "--json"],
          None, action="list", contains=("t_alpha",)),
    _read("list-count-sorted-asc",
          ["memory", "list", "--workspace", WORKSPACE, "--sort", "deliberate",
           "--order", "asc", "--format", "count", "--limit", "50",
           "--type", "project", "--class", "thread", "--status", "open",
           "--audience", "any"],
          None, action="list", contains=("1",)),
    _read("stats", ["memory", "stats", "--json"],
          None, action="stats", contains=("{",)),
    _read("conflicts", ["memory", "conflicts", "--json", "--threshold", "0.9"],
          None, action="conflicts", contains=("conflicts",)),
    _read("episode-show", ["memory", "episode-show", EPISODE_ID, "--json"],
          None, action="episode-show", contains=(EPISODE_ID,)),

    # -- gaia memory: the caller names the row ------------------------------
    _read("show-text", ["memory", "show", "t_alpha", "--workspace", WORKSPACE],
          DELIBERATE, ["t_alpha"], action="show", contains=("t_alpha",)),
    _read("show-json",
          ["memory", "show", "t_alpha", "--workspace", WORKSPACE, "--json"],
          DELIBERATE, ["t_alpha"], action="show", contains=("t_alpha",)),
    _read("show-links-text",
          ["memory", "show", "t_alpha", "--workspace", WORKSPACE, "--links"],
          DELIBERATE, ["t_alpha"], action="show", contains=("Links",)),
    _read("show-history-text",
          ["memory", "show", "t_alpha", "--workspace", WORKSPACE, "--history"],
          DELIBERATE, ["t_alpha"], action="show", contains=("History",)),
    _read("show-missing-slug",
          ["memory", "show", "no_such_slug", "--workspace", WORKSPACE, "--json"],
          None, action="show", rc=1, contains=("not found",)),
    _read("story-text",
          ["memory", "story", "t_alpha", "--workspace", WORKSPACE,
           "--max-depth", "3"],
          DELIBERATE, ["t_alpha"], action="story", contains=("Timeline",)),
    _read("story-json",
          ["memory", "story", "t_alpha", "--workspace", WORKSPACE, "--json"],
          DELIBERATE, ["t_alpha"], action="story", contains=("t_alpha",)),

    # -- gaia memory get-relevant: automatic blocks -------------------------
    _read("get-relevant-digest",
          ["memory", "get-relevant", "--workspace", WORKSPACE,
           "--max-chars", "4000"],
          INJECTION, ["t_alpha", "t_beta"], action="get-relevant",
          contains=("t_alpha", "t_beta")),
    _read("get-relevant-sections",
          ["memory", "get-relevant", "--workspace", WORKSPACE,
           "--sections", "carry_forward,anchor,thread_open",
           "--max-chars", "4000", "--no-pointer"],
          INJECTION, ["t_alpha", "t_beta", "a_anchor", "u_kernel_and_digest"],
          action="get-relevant",
          contains=("t_alpha", "t_beta", "a_anchor", "u_kernel_and_digest")),
    _read("get-relevant-types",
          ["memory", "get-relevant", "--workspace", WORKSPACE,
           "--types", "atom", "--limit", "8"],
          INJECTION, ["atom_seeded"], action="get-relevant", contains=("atom_seeded",)),

    # -- gaia memory get-relevant: the caller names the initiative ----------
    _read("get-relevant-initiative-text",
          ["memory", "get-relevant", "--workspace", WORKSPACE,
           "--initiative", "alpha"],
          DELIBERATE, ["t_alpha"], action="get-relevant", contains=("t_alpha",)),
    _read("get-relevant-initiative-json",
          ["memory", "get-relevant", "--workspace", WORKSPACE,
           "--initiative", "alpha", "--json"],
          DELIBERATE, ["t_alpha"], action="get-relevant", contains=("t_alpha",)),

    # -- gaia query: the substrate's event reader ---------------------------
    #
    # The bodies ARE in the JSON dump (`raw`), and the counters still do not
    # move: a window over the table names none of the rows it returns.
    _read("query-table",
          ["query", "--surface", "memory", "--workspace", WORKSPACE,
           "--last", "50"],
          None, action=None, contains=("t_alpha",)),
    _read("query-json",
          ["query", "--surface", "memory", "--workspace", WORKSPACE,
           "--last", "50", "--json"],
          None, action=None, contains=("t_alpha", "\"body\"")),
    _read("query-format-json",
          ["query", "--surface", "memory", "--workspace", WORKSPACE,
           "--last", "50", "--format", "json", "--snippets", "--type", "project"],
          None, action=None, contains=("t_alpha",)),
    _read("query-count",
          ["query", "--surface", "memory", "--workspace", WORKSPACE,
           "--count", "--group-by", "surface"],
          None, action=None, contains=("memory",)),
    _read("query-format-count",
          ["query", "--surface", "memory", "--workspace", WORKSPACE,
           "--format", "count"],
          None, action=None, contains=(r"^[1-9][0-9]*$",)),
    _read("query-group-by",
          ["query", "--surface", "memory", "--workspace", WORKSPACE,
           "--group-by", "type"],
          None, action=None, contains=("project",)),
    _read("query-all-surfaces",
          ["query", "--surface", "all", "--workspace", WORKSPACE,
           "--last", "50", "--json"],
          None, action=None, contains=("t_alpha", EPISODE_ID)),
    _read("query-metrics",
          ["query", "--metrics", "--workspace", WORKSPACE, "--json",
           "--since", "3650d", "--until", "3650d"],
          None, action=None, contains=(r"^\[",)),

    # -- gaia memory: write verbs -------------------------------------------
    #
    # A write is not a read: authoring a row is not someone going to look for
    # it, so no write verb touches either counter.
    _read("add",
          ["memory", "add", "--name", "w_added", "--type", "project",
           "--body", "added body", "--description", "added",
           "--workspace", WORKSPACE, "--initiative", "alpha",
           "--class", "log", "--json"],
          None, action="add", contains=("w_added",)),
    _read("edit",
          ["memory", "edit", "--name", "w_edit", "--field", "description",
           "--content", "edited", "--workspace", WORKSPACE, "--json"],
          None, action="edit", contains=("w_edit",)),
    _read("append",
          ["memory", "append", "w_append", "--body", "appended",
           "--workspace", WORKSPACE, "--json"],
          None, action="append", contains=("w_append",)),
    _read("reclassify",
          ["memory", "reclassify", "w_reclass", "--class", "thread",
           "--status", "closed", "--workspace", WORKSPACE, "--json"],
          None, action="reclassify", contains=("w_reclass",)),
    _read("link",
          ["memory", "link", "w_link_src", "w_link_dst", "--kind",
           "derived_from", "--workspace", WORKSPACE, "--json"],
          None, action="link", contains=("w_link_src",)),
    _read("delete",
          ["memory", "delete", "w_delete", "--yes", "--workspace", WORKSPACE,
           "--json"],
          None, action="delete", contains=("w_delete",)),
    _read("checkpoint",
          ["memory", "checkpoint", "--file", "__CHECKPOINT_PAYLOAD__",
           "--workspace", WORKSPACE, "--json"],
          None, action="checkpoint", contains=("applied",)),
)

#: Flags that shape neither the row set nor the output shape, so exercising
#: them could not change a verdict. Everything else must appear in a recipe.
NON_CLASSIFYING_FLAGS: dict[str, frozenset[str]] = {
    "search": frozenset({"--workspace"}),
    "stats": frozenset(),
    "show": frozenset({"--workspace"}),
    "episode-show": frozenset(),
    "list": frozenset({"--workspace"}),
    "delete": frozenset({"--workspace", "--hard"}),
    "edit": frozenset({"--workspace", "--append", "--audience", "--body-file",
                       "--class", "--project", "--project-ref", "--status"}),
    "append": frozenset({"--workspace", "--body-file"}),
    "reclassify": frozenset({"--workspace"}),
    "link": frozenset({"--workspace", "--delete"}),
    "add": frozenset({"--workspace", "--audience", "--body-file", "--project",
                      "--project-ref", "--status"}),
    "checkpoint": frozenset({"--workspace", "--project", "--project-ref"}),
    "get-relevant": frozenset({"--workspace"}),
    "conflicts": frozenset(),
    "story": frozenset({"--workspace"}),
}

#: Modules allowed to call ``record_memory_access``, each mapped to the
#: surface recipes that measure what it does.
BUMP_CALL_SITES: dict[str, tuple[str, ...]] = {
    "bin/cli/memory.py": (
        "get-relevant-digest", "get-relevant-sections", "get-relevant-types",
        "get-relevant-initiative-text", "show-text",
    ),
    "bin/cli/memory_story.py": ("story-text",),
    "hooks/modules/context/kernel_builder.py": ("kernel-memory-block",),
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def seeded(tmp_path_factory) -> dict:
    """A fresh scratch substrate carrying the corpus above, all counters at
    zero -- built PER TEST, not shared across the module.

    Function-scoped on purpose: several recipes in ``SURFACES`` are real
    writes (``checkpoint`` mints a brand-new ``class=anchor`` row,
    ``add``/``reclassify``/``edit``/``link``/``delete`` mutate the corpus
    too), and more than one read surface queries its class/type UNSCOPED
    (the anchor section is "every ``class='anchor'`` row in the workspace",
    not "the two rows this test seeded"). A module-scoped fixture let an
    earlier case's write survive into a later case's exact-row assertion --
    e.g. ``checkpoint``'s ``w_ckpt`` leaking into the anchor section a later
    test reads -- so which cases passed depended on execution order (file
    order under one worker, scattered/interleaved under `-n auto`, and
    guaranteed to break the moment collection order changes). A private
    corpus per test removes the shared mutable surface instead of tolerating
    it: whatever a case writes dies with that case's own database.
    """
    data_dir = tmp_path_factory.mktemp("gaia_data")
    saved = {k: os.environ.get(k) for k in
             ("GAIA_DATA_DIR", "GAIA_DB", "GAIA_DISPATCH_AGENT")}
    os.environ["GAIA_DATA_DIR"] = str(data_dir)
    os.environ.pop("GAIA_DB", None)
    os.environ.pop("GAIA_DISPATCH_AGENT", None)
    try:
        from gaia.paths import db_path
        db = db_path()
        from gaia.store.writer import reclassify_memory, upsert_memory
        for seed in SEEDS:
            upsert_memory(
                WORKSPACE, seed.name, type=seed.type,
                body=f"body of {seed.name}, seeded for the surface census",
                description=f"description of {seed.name}",
                initiative=seed.initiative, audience=seed.audience,
                db_path=db,
            )
            if seed.class_ or seed.status:
                reclassify_memory(WORKSPACE, seed.name, class_=seed.class_,
                                  status=seed.status, db_path=db)
        _seed_episode(db)
        payload = data_dir / "checkpoint.json"
        payload.write_text(json.dumps({
            "resumen": {"name": "w_ckpt", "type": "project",
                        "description": "checkpoint anchor",
                        "body": "checkpoint anchor body"},
            "pendientes": [],
        }), encoding="utf-8")
        yield {"db": db, "data_dir": data_dir, "checkpoint_payload": payload}
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _seed_episode(db: Path) -> None:
    from gaia.store.writer import _connect
    con = _connect(db)
    try:
        con.execute(
            "INSERT OR REPLACE INTO episodes "
            "(episode_id, workspace, timestamp, agent, type, title, prompt) "
            "VALUES (?, ?, '2026-08-13T00:00:00Z', 'gaia-system', 'task', "
            "        'seeded episode', 'seeded prompt')",
            (EPISODE_ID, WORKSPACE),
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

def _counters(db: Path) -> dict[str, tuple[int, int, int]]:
    """(injection_count, deliberate_count, kernel_count) per row name."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {
            name: (injection, deliberate, kernel)
            for name, injection, deliberate, kernel in con.execute(
                "SELECT name, injection_count, deliberate_count, kernel_count "
                "FROM memory WHERE workspace = ?", (WORKSPACE,)
            )
        }
    finally:
        con.close()


def _moved(before: dict, after: dict) -> dict[str, tuple[int, int, int]]:
    out = {}
    for name, (injection, deliberate, kernel) in after.items():
        was = before.get(name, (0, 0, 0))
        delta = (injection - was[0], deliberate - was[1], kernel - was[2])
        if delta != (0, 0, 0):
            out[name] = delta
    return out


def _run(argv, seeded) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GAIA_DATA_DIR"] = str(seeded["data_dir"])
    env.pop("GAIA_DB", None)
    env.pop("GAIA_DISPATCH_AGENT", None)
    resolved = [
        str(seeded["checkpoint_payload"]) if a == "__CHECKPOINT_PAYLOAD__" else a
        for a in argv
    ]
    return subprocess.run(
        [sys.executable, str(_GAIA), *resolved],
        capture_output=True, text=True, env=env, timeout=120, check=False,
    )


# ---------------------------------------------------------------------------
# The census
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("surface", SURFACES, ids=lambda s: s.surface_id)
def test_surface_moves_exactly_the_counter_its_verdict_declares(surface, seeded):
    before = _counters(seeded["db"])
    result = _run(surface.argv, seeded)
    after = _counters(seeded["db"])

    assert result.returncode == surface.returncode, (
        f"{surface.surface_id}: rc={result.returncode}\n"
        f"stdout: {result.stdout[:2000]}\nstderr: {result.stderr[:2000]}"
    )
    combined = result.stdout + result.stderr
    for fragment in surface.output_matches:
        assert re.search(fragment, combined, re.MULTILINE), (
            f"{surface.surface_id}: output does not match {fragment!r}, so a "
            f"zero delta would prove nothing.\nstdout: {result.stdout[:2000]}"
        )

    moved = _moved(before, after)
    assert set(moved) == set(surface.rows), (
        f"{surface.surface_id} is classified {surface.kind or 'neither'}: "
        f"expected rows {sorted(surface.rows)} to move, measured "
        f"{ {k: v for k, v in sorted(moved.items())} }"
    )
    if surface.kind is not None:
        expected_delta = _EXPECTED_DELTA[surface.kind]
        for name, delta in moved.items():
            assert delta == expected_delta, (
                f"{surface.surface_id}: row {name} moved {delta} "
                f"(injection, deliberate, kernel); its verdict {surface.kind} "
                f"demands {expected_delta}"
            )


def test_kernel_memory_block_counts_the_rows_it_renders(seeded):
    """Context assembly's one memory renderer: kernel axis, body-less name
    and all -- never injection, the axis it shared before this split. Every
    type=user/audience=executor row in the corpus renders together, so both
    seeded rows of that shape move -- not only ``u_exec``."""
    from hooks.modules.context.kernel_builder import build_memory_block

    before = _counters(seeded["db"])
    block = build_memory_block(WORKSPACE, db_path=seeded["db"])
    after = _counters(seeded["db"])

    assert "body of u_exec" in block
    assert _moved(before, after) == {
        "u_exec": (0, 0, 1),
        "u_kernel_and_digest": (0, 0, 1),
    }


def test_kernel_dispatch_and_context_digest_move_disjoint_axes_on_the_same_row(
    seeded,
):
    """Gate 791 / AC-2, on ONE row both surfaces can reach: type=user AND
    audience=executor (the kernel block's own query) with class=anchor so
    get-relevant's anchor section -- which pins type=user rows to the top --
    selects it too. A simulated subagent dispatch (the kernel's own memory
    block, the exact function SubagentStart renders) must move kernel_count
    ONLY. A session-context surface (get-relevant --sections anchor) over the
    SAME row must move injection_count ONLY. deliberate_count must move in
    NEITHER case. Both writes stay outside updated_at and memory_history."""
    from hooks.modules.context.kernel_builder import build_memory_block

    row_name = "u_kernel_and_digest"

    def _row_updated_at() -> str:
        con = sqlite3.connect(f"file:{seeded['db']}?mode=ro", uri=True)
        try:
            return con.execute(
                "SELECT updated_at FROM memory WHERE workspace = ? AND name = ?",
                (WORKSPACE, row_name),
            ).fetchone()[0]
        finally:
            con.close()

    def _history_count() -> int:
        con = sqlite3.connect(f"file:{seeded['db']}?mode=ro", uri=True)
        try:
            return con.execute(
                "SELECT COUNT(*) FROM memory_history WHERE workspace = ?",
                (WORKSPACE,),
            ).fetchone()[0]
        finally:
            con.close()

    updated_at_before = _row_updated_at()
    history_before = _history_count()

    # 1) Simulate a subagent dispatch: the kernel block. Every
    # type=user/audience=executor row renders together, so `u_exec` (the
    # OTHER seeded row of that shape) moves alongside `row_name` -- both on
    # the kernel axis, neither on injection or deliberate.
    before_dispatch = _counters(seeded["db"])
    block = build_memory_block(WORKSPACE, db_path=seeded["db"])
    after_dispatch = _counters(seeded["db"])
    assert f"body of {row_name}" in block
    assert _moved(before_dispatch, after_dispatch) == {
        row_name: (0, 0, 1),
        "u_exec": (0, 0, 1),
    }

    # 2) A real session-context surface, over the SAME row. The other seeded
    # anchor row (`a_anchor`) shares this section too -- both move on
    # injection, neither on kernel or deliberate.
    result = _run(
        ["memory", "get-relevant", "--workspace", WORKSPACE,
         "--sections", "anchor", "--max-chars", "4000", "--no-pointer"],
        seeded,
    )
    assert result.returncode == 0, result.stderr[:2000]
    assert row_name in result.stdout
    after_digest = _counters(seeded["db"])
    assert _moved(after_dispatch, after_digest) == {
        row_name: (1, 0, 0),
        "a_anchor": (1, 0, 0),
    }

    # Neither surface ever touched deliberate_count, updated_at, or
    # memory_history for this row.
    assert after_digest[row_name][1] == before_dispatch[row_name][1]
    assert _row_updated_at() == updated_at_before
    assert _history_count() == history_before


def test_telemetry_never_touches_the_audited_columns(seeded):
    """The narrow UPDATE: no updated_at movement, no memory_history row --
    for all three kinds, not only the two that predate this split."""
    con = sqlite3.connect(f"file:{seeded['db']}?mode=ro", uri=True)
    try:
        before_updated = dict(con.execute(
            "SELECT name, updated_at FROM memory WHERE workspace = ?",
            (WORKSPACE,)))
        before_history = con.execute(
            "SELECT COUNT(*) FROM memory_history WHERE workspace = ?",
            (WORKSPACE,)).fetchone()[0]
    finally:
        con.close()

    from gaia.store.writer import record_memory_access
    assert record_memory_access(WORKSPACE, "t_alpha", DELIBERATE,
                                db_path=seeded["db"]) is True
    assert record_memory_access(WORKSPACE, "t_alpha", INJECTION,
                                db_path=seeded["db"]) is True
    assert record_memory_access(WORKSPACE, "t_alpha", KERNEL,
                                db_path=seeded["db"]) is True

    con = sqlite3.connect(f"file:{seeded['db']}?mode=ro", uri=True)
    try:
        after_updated = dict(con.execute(
            "SELECT name, updated_at FROM memory WHERE workspace = ?",
            (WORKSPACE,)))
        after_history = con.execute(
            "SELECT COUNT(*) FROM memory_history WHERE workspace = ?",
            (WORKSPACE,)).fetchone()[0]
    finally:
        con.close()

    assert after_updated == before_updated
    assert after_history == before_history


def test_telemetry_failure_never_reaches_the_read(tmp_path):
    """Best-effort: an unopenable substrate reports False, raises nothing --
    for all three kinds."""
    from gaia.store.writer import record_memory_access
    # A directory where a database file belongs: sqlite3 cannot open it, and
    # unlike a missing path the store layer will not create it either.
    assert record_memory_access(
        WORKSPACE, "t_alpha", DELIBERATE, db_path=tmp_path) is False
    assert record_memory_access(
        WORKSPACE, "t_alpha", INJECTION, db_path=tmp_path) is False
    assert record_memory_access(
        WORKSPACE, "t_alpha", KERNEL, db_path=tmp_path) is False


def test_invalid_access_kind_is_still_rejected(seeded):
    """The map now admits a third key; a typo must still raise, not write
    silently anywhere -- this is the guard task instruction #1 asks to keep."""
    from gaia.store.writer import record_memory_access

    with pytest.raises(ValueError):
        record_memory_access(WORKSPACE, "t_alpha", "kernle", db_path=seeded["db"])

    before = _counters(seeded["db"])
    with pytest.raises(ValueError):
        record_memory_access(WORKSPACE, "t_alpha", "bogus", db_path=seeded["db"])
    after = _counters(seeded["db"])
    assert before == after


# ---------------------------------------------------------------------------
# Guards against the census going stale
# ---------------------------------------------------------------------------

def _memory_subparsers() -> dict:
    from cli import memory as memory_mod
    parser = argparse.ArgumentParser(prog="gaia")
    subparsers = parser.add_subparsers(dest="cmd")
    memory_mod.register(subparsers)
    group = [
        action for action in subparsers.choices["memory"]._subparsers._group_actions
        if action.choices
    ][0]
    return dict(group.choices)


def _query_parser():
    from cli import query as query_mod
    parser = argparse.ArgumentParser(prog="gaia")
    subparsers = parser.add_subparsers(dest="cmd")
    query_mod.register(subparsers)
    return subparsers.choices["query"]


def _flags_in(argv) -> set[str]:
    return {token.split("=", 1)[0] for token in argv if token.startswith("--")}


def test_every_memory_action_is_classified():
    """A new `gaia memory` subcommand fails here until a recipe measures it."""
    declared = {s.action for s in SURFACES if s.action}
    assert set(_memory_subparsers()) == declared


def test_every_flag_is_exercised_or_declared_non_classifying():
    """A new flag on a memory subcommand fails here until a recipe covers it.

    Flag-level, because that is where this family's defects have landed:
    `show --links` and `--initiative` in text mode were both wrong while the
    subcommands around them were right.
    """
    unclassified: dict[str, set[str]] = {}
    for action, parser in _memory_subparsers().items():
        available = {
            option
            for parser_action in parser._actions
            for option in parser_action.option_strings
            if option.startswith("--") and option != "--help"
        }
        exercised = {
            flag
            for surface in SURFACES
            if surface.action == action
            for flag in _flags_in(surface.argv)
        }
        missing = available - exercised - NON_CLASSIFYING_FLAGS.get(action, frozenset())
        if missing:
            unclassified[action] = missing
    assert unclassified == {}


def test_every_query_output_shape_is_exercised():
    """`gaia query` counts under no shape, so every shape it can take is run."""
    shaping = {"--surface", "--format", "--json", "--count", "--group-by",
               "--metrics", "--snippets"}
    available = {
        option
        for parser_action in _query_parser()._actions
        for option in parser_action.option_strings
        if option in shaping
    }
    exercised = {
        flag
        for surface in SURFACES
        if surface.argv and surface.argv[0] == "query"
        for flag in _flags_in(surface.argv)
    }
    assert available - exercised == set()


def test_every_bump_call_site_belongs_to_a_classified_surface():
    """A bump wired into an unmeasured module fails here."""
    pattern = re.compile(
        r"record_memory_access|_bump_memory_telemetry|_bump_injection_telemetry"
        r"|_record_kernel_telemetry"
    )
    found: set[str] = set()
    for root in ("bin", "hooks", "tools", "gaia", "scripts"):
        for path in (_REPO_ROOT / root).rglob("*.py"):
            relative = path.relative_to(_REPO_ROOT).as_posix()
            if relative == "gaia/store/writer.py":
                continue
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                found.add(relative)
    assert found == set(BUMP_CALL_SITES)


def test_declared_surfaces_cover_every_seeded_row_once():
    """Every seeded row is reached by some surface, so none is dead weight."""
    reached = {row for surface in SURFACES for row in surface.rows}
    reached.add("u_exec")  # test_kernel_memory_block_counts_the_rows_it_renders
    reached.update(s.name for s in SEEDS if s.name.startswith("w_"))
    assert {s.name for s in SEEDS} == reached
