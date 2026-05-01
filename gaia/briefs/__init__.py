"""
gaia.briefs -- Brief and plan storage on top of the Gaia SQLite substrate (B8).

This module owns:
  - Frontmatter + body parser/serializer (markdown <-> dict round-trip)
  - Store API for briefs (insert, update, list, show, close, deps, search)
  - Import-from-fs for migrating existing /home/jorge/ws/me/briefs/*/brief.md

Storage layer: ``~/.gaia/gaia.db`` via gaia.store.writer._connect (B1).
Schema lives in gaia/store/schema.sql (briefs, acceptance_criteria, milestones,
brief_dependencies, plans, tasks, briefs_fts).

Public API::

    from gaia.briefs import (
        parse_brief_markdown,
        serialize_brief_to_markdown,
        upsert_brief,
        list_briefs,
        get_brief,
        close_brief,
        get_dependencies,
        search_briefs,
        import_from_fs,
    )
"""

from gaia.briefs.serializer import (
    parse_brief_markdown,
    serialize_brief_to_markdown,
)
from gaia.briefs.store import (
    upsert_brief,
    list_briefs,
    get_brief,
    close_brief,
    get_dependencies,
    search_briefs,
    import_from_fs,
    delete_brief,
)

__all__ = [
    "parse_brief_markdown",
    "serialize_brief_to_markdown",
    "upsert_brief",
    "list_briefs",
    "get_brief",
    "close_brief",
    "get_dependencies",
    "search_briefs",
    "import_from_fs",
    "delete_brief",
]
