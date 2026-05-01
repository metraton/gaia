"""
gaia.store -- SQLite substrate for Gaia workspace context.

Public API::

    from gaia.store import (
        upsert_repo,
        upsert_app,
        delete_missing_in,
        bulk_upsert,
        wipe_project,
    )
    from gaia.store.provider import get_context

Patterns inspired by engram (https://github.com/koaning/engram), MIT License.
No runtime dependency on engram; patterns lifted with attribution (see NOTICE.md).
"""

from gaia.store.writer import (
    bulk_upsert,
    delete_missing_in,
    save_integration,
    upsert_app,
    upsert_repo,
    wipe_project,
)
from gaia.store.provider import get_context

__all__ = [
    "upsert_repo",
    "upsert_app",
    "delete_missing_in",
    "bulk_upsert",
    "wipe_project",
    "save_integration",
    "get_context",
]
