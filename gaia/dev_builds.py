"""
gaia.dev_builds -- per-machine count of local dev iterations over a base version.

The problem this solves: every `gaia dev` build ships the SAME semver as the
released base it was packed from (see ``gaia.hooks_build`` for why the packed
``package.json`` version is deliberately never bumped), so "Gaia: 5.3.0" cannot
tell the user whether they are on the pristine release or on their eleventh
local iteration of it. The five version sources the release gate cross-checks
(``package.json``, ``pyproject.toml``, ``.claude-plugin/plugin.json``,
``.claude-plugin/marketplace.json``, the ``CHANGELOG.md`` header --
``bin/pre-publish-validate.js`` requires all five to agree) are therefore NOT
where the iteration count can live: writing there would both break the release
gate and dirty a git-tracked file on every dev run. This sidecar is the answer
-- state, not source.

Identity of a build: the counter is keyed by BASE VERSION and advanced only
when the hooks tree's content digest (``gaia.hooks_build.hooks_content_hash``)
differs from the one last recorded for that version. That is what makes the
count mean "distinct builds", not "times the command ran": a repack whose
packaged bytes are unchanged (commits touching only ``tests/`` or
``.github/``, which ``npm pack`` excludes) produces the identical digest and
must not inflate the count. Keying by version rather than accumulating one
global counter means a base-version bump naturally starts its own sequence.

Scope: per MACHINE, not per workspace. ``gaia dev`` packs ONE source tree per
run; the iteration is a property of that source tree's build history, so it
lives under ``gaia.paths.state_dir()`` rather than under a workspace key.

Every reader degrades to silence rather than failing: an absent, corrupt, or
unreadable sidecar makes ``describe_version`` return the bare base version, so
the SessionStart manifest keeps rendering ``Gaia: 5.3.0`` exactly as it did
before this module existed. The same discipline the memory block follows.

Home rationale: this module lives in the ``gaia`` package for the same reason
``gaia.hooks_build`` does -- it is the ONE import root reachable from BOTH
callers, the SessionStart hook and ``bin/cli/*``.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Sidecar filename under ``gaia.paths.state_dir()``.
SIDECAR_NAME = "dev-builds.json"

#: Payload schema version. Bumped only on an incompatible layout change; a
#: reader that does not recognise the value treats the whole file as absent.
SCHEMA_VERSION = 1


def sidecar_path() -> Path:
    """Return the absolute path of the dev-build counter sidecar.

    Resolved through ``gaia.paths.state_dir()`` on every call, so a
    ``GAIA_DATA_DIR`` override (tests, sandboxes) relocates it.
    """
    from gaia.paths import state_dir

    return state_dir() / SIDECAR_NAME


def _load(path: Path) -> dict:
    """Return the sidecar payload, or an empty payload on any failure.

    Absent, unreadable, non-JSON, wrong-shaped, and unknown-schema files are
    all indistinguishable to callers: they yield ``{"version": SCHEMA_VERSION,
    "builds": {}}``. Never raises -- a corrupt counter must never be able to
    break the caller that reads it.
    """
    empty: dict[str, Any] = {"version": SCHEMA_VERSION, "builds": {}}
    try:
        if not path.is_file():
            return empty
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("dev_builds: unreadable sidecar at %s (%s)", path, exc)
        return empty

    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        return empty
    builds = data.get("builds")
    if not isinstance(builds, dict):
        return empty
    return {"version": SCHEMA_VERSION, "builds": builds}


def _save(path: Path, payload: dict) -> bool:
    """Write *payload* atomically. Returns True on success, False on failure.

    Mirrors ``session_registry._save_registry``: a per-call tmp sibling then
    ``os.rename``, so a concurrent reader never observes a partial write and
    two concurrent writers never share a tmp path. Failure is reported by the
    return value, never by an exception -- the caller is a dev-loop side
    effect that must not be able to fail the build it is counting.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp.{os.getpid()}.{os.urandom(4).hex()}")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.rename(str(tmp), str(path))
        return True
    except Exception as exc:
        logger.debug("dev_builds: write failed for %s (%s)", path, exc)
        try:
            tmp.unlink()
        except Exception:
            pass
        return False


def read_record(base_version: str) -> Optional[dict]:
    """Return the recorded dev-build record for *base_version*, else None.

    A record is ``{"count": int, "hooks_hash": str, "updated_at": str}``. None
    means "no usable record": no sidecar, no entry for this version, or an
    entry whose ``count`` is not a positive int. Never raises.
    """
    try:
        if not base_version:
            return None
        record = _load(sidecar_path())["builds"].get(base_version)
        if not isinstance(record, dict):
            return None
        count = record.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            return None
        return record
    except Exception as exc:
        logger.debug("dev_builds: read_record failed (%s)", exc)
        return None


def record_build(base_version: str, hooks_hash: str) -> Optional[dict]:
    """Register a dev build of *base_version* whose hooks digest is *hooks_hash*.

    Increments the version's counter ONLY when *hooks_hash* differs from the
    digest last recorded for it, so a repack that produced byte-identical
    packaged content leaves the count untouched (and writes nothing at all).
    A first build of a version starts at 1.

    An empty *hooks_hash* -- what ``hooks_content_hash`` returns for a tree it
    could not digest -- is not an identity, so it is never stored: the existing
    record is returned unchanged.

    Returns the record now in effect, or None when there is nothing to report
    (no version, or the write failed). Never raises.
    """
    try:
        if not base_version:
            return None

        current = read_record(base_version)
        if not hooks_hash:
            return current
        if current is not None and current.get("hooks_hash") == hooks_hash:
            return current

        path = sidecar_path()
        payload = _load(path)
        count = (current.get("count", 0) if current else 0) + 1
        record = {
            "count": count,
            "hooks_hash": hooks_hash,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        payload["builds"][base_version] = record
        if not _save(path, payload):
            return None
        return record
    except Exception as exc:
        logger.debug("dev_builds: record_build failed (%s)", exc)
        return None


def format_label(base_version: str, record: Optional[dict]) -> str:
    """Render *base_version* annotated with *record*, e.g.
    ``5.3.0 (dev.7, build fb27693c)``.

    Falls back to the bare *base_version* whenever *record* carries no usable
    count -- the pristine-release rendering, and the degraded rendering, are
    deliberately the same string.
    """
    try:
        if not isinstance(record, dict):
            return base_version
        count = record.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            return base_version
        digest = record.get("hooks_hash")
        if isinstance(digest, str) and digest:
            return f"{base_version} (dev.{count}, build {digest})"
        return f"{base_version} (dev.{count})"
    except Exception:
        return base_version


def describe_version(base_version: str) -> str:
    """Return *base_version* annotated with its dev-build count, if any.

    The single entry point every display surface calls (SessionStart manifest,
    ``gaia doctor``, ``gaia dev``'s closing line). Degrades to the bare
    *base_version* on any failure, so no surface can be broken by the counter.
    """
    try:
        return format_label(base_version, read_record(base_version))
    except Exception:
        return base_version


__all__ = [
    "SIDECAR_NAME",
    "SCHEMA_VERSION",
    "sidecar_path",
    "read_record",
    "record_build",
    "format_label",
    "describe_version",
]
