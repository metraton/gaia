"""
gaia.identity_shape -- canonical classifier for a ``project_identity`` payload.

The ``project_identity`` project-context contract stores a project's identity
in one of a few structural shapes, and two independent consumers must agree on
which shape a payload is:

  * ``tools/scan/promote.py`` -- decides how to MERGE scan-owned facts into an
    existing contract (map merge vs. flat refresh vs. auto-conversion).
  * ``hooks/modules/session/session_manifest.py`` -- decides how to READ the
    payload back into the SessionStart Projects block.

Both used to carry their own inline predicate, and they drifted: the reader
already treated the scanner ``workspace_repos`` form as distinct from a flat
single-project contract, while promotion collapsed both into one ``flat``
bucket. That collapse is the latent bug this module closes -- a scanner-shape
contract classified as ``flat`` would be fed to the flat single-project refresh
path, which writes scan-owned keys (local_path, remote_url, ...) at the TOP
level of a payload that legitimately holds ``name`` + ``workspace_repos`` +
``monorepo``, corrupting it.

The four shapes:

  * ``empty``   -- not a dict, or an empty dict; nothing to merge or read.
  * ``map``     -- keyed by project slug, every value a dict, no top-level
                   ``name``. The canonical multi-project shape.
  * ``scanner`` -- a top-level ``name`` PLUS a ``workspace_repos`` list; the
                   multi-repo workspace form the scanner emits. Read specially
                   by the session manifest; never flat-refreshed by promotion.
  * ``flat``    -- a top-level ``name`` with NO ``workspace_repos``; the
                   single-project / workspace-identity form (often
                   hand-authored). This is the only shape the flat refresh path
                   (``_merge_flat``) may touch.

The reserved key :data:`WORKSPACE_META_KEY` is where promotion parks the old
top-level metadata of a ``flat``/``scanner`` payload when it auto-converts to a
``map``, so hand-authored workspace-level data is preserved rather than lost. A
map key starting with ``_`` is reserved and never a project entry.
"""

from __future__ import annotations

from typing import Optional

# Reserved map key holding workspace-level metadata preserved across an
# auto-conversion from a flat/scanner shape to a map. The leading underscore
# marks it (and any other `_`-prefixed key) as a non-project reserved slot.
WORKSPACE_META_KEY = "_workspace"


def is_reserved_slug(slug: str) -> bool:
    """True when a map key is a reserved slot, not a project entry."""
    return isinstance(slug, str) and slug.startswith("_")


def classify_identity_shape(payload: Optional[dict]) -> str:
    """Classify a ``project_identity`` payload as one of the four shapes.

    Returns ``"empty"`` | ``"map"`` | ``"scanner"`` | ``"flat"``. The order of
    the checks is load-bearing: a map is recognized first (no top-level
    ``name``), then the scanner form (``name`` + ``workspace_repos`` list),
    leaving only the single-project/workspace-identity ``flat`` form.
    """
    if not isinstance(payload, dict) or not payload:
        return "empty"

    is_map = (
        "name" not in payload
        and all(isinstance(v, dict) for v in payload.values())
        and any(
            ("local_path" in v or "name" in v)
            for v in payload.values()
            if isinstance(v, dict)
        )
    )
    if is_map:
        return "map"

    if isinstance(payload.get("workspace_repos"), list):
        return "scanner"

    return "flat"
