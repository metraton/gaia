"""
install_detector.py -- Detect install/auth operations in tool output.

Called from the subagent_stop pipeline to auto-capture workspace integrations
after npm install, pip install, gaia install, auth configure, etc.

Public API::

    detect(tool_output: str) -> dict
        Returns: {"matched": bool, "pattern": str, "target": str}
        On no match: {"matched": False}

    resolve_workspace() -> str
        Returns the current workspace identity via gaia.project.current().
        Pure delegation -- no fallback logic implemented here (B0 owns that).

    build_topic_key(kind: str, target: str) -> str
        Returns: "{kind}/{family}/{target}" e.g. "cli/atlassian/acli"
        Used for idempotent upserts in the integrations table.
"""

from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Install pattern definitions
# ---------------------------------------------------------------------------

# Each pattern: (pattern_label, compiled_regex, kind, target_group_index)
# target_group_index: which regex group contains the package/tool name (1-based)
#
# Ordering matters: more specific patterns first to avoid partial matches.
_INSTALL_PATTERNS = [
    # npm install -g <pkg>  (global install)
    ("npm install", re.compile(r"npm\s+install\s+-g\s+([A-Za-z0-9@._/-]+)", re.IGNORECASE), "cli", 1),
    # npm install <pkg>  (local install, no -g flag)
    ("npm install", re.compile(r"npm\s+install\s+([A-Za-z0-9@._/-]+)", re.IGNORECASE), "pkg", 1),
    # pip install <pkg>
    ("pip install", re.compile(r"pip(?:3)?\s+install\s+([A-Za-z0-9._-]+)", re.IGNORECASE), "pkg", 1),
    # gaia install <pkg>
    ("gaia install", re.compile(r"gaia\s+install\s+([A-Za-z0-9._-]+)", re.IGNORECASE), "cli", 1),
    # <svc> auth configure  (e.g. gcloud auth configure, aws configure, acli auth configure)
    ("auth configure", re.compile(r"([A-Za-z][A-Za-z0-9._-]*)\s+auth\s+configure", re.IGNORECASE), "cli", 1),
]

# ---------------------------------------------------------------------------
# Family mapping: target name -> family label for topic_key
# ---------------------------------------------------------------------------

_FAMILY_MAP: dict[str, str] = {
    # Atlassian CLI
    "acli": "atlassian",
    "@atlassian/acli": "atlassian",
    # Google Cloud
    "gcloud": "google",
    "google-cloud-sdk": "google",
    # AWS
    "awscli": "aws",
    "aws-cli": "aws",
    # Kubernetes
    "kubectl": "kubernetes",
    "helm": "kubernetes",
    # HashiCorp
    "terraform": "hashicorp",
    "vault": "hashicorp",
    # Gaia
    "gaia": "gaia",
    "@jaguilar87/gaia": "gaia",
    # npm generic (no mapping -> falls through to "generic")
}


def _normalize_target(raw: str) -> str:
    """Strip version specifiers and scope chars from a package name.

    Examples:
        "@scope/pkg@1.0.0" -> "pkg"   (scoped npm, version stripped)
        "pkg==1.0.0"        -> "pkg"   (pip with version pin)
        "pkg>=2"            -> "pkg"
        "-g"                -> ""      (flag leaked through)
    """
    # Strip leading/trailing whitespace
    target = raw.strip()

    # Handle scoped npm packages like @atlassian/acli -> use the last segment
    if target.startswith("@") and "/" in target:
        # @scope/name -> keep @scope/name but strip version
        target = target.split("@")[0] + "@" + target.split("@")[1] if target.count("@") > 1 else target
        # Simpler: keep basename for family lookup
        bare = target.lstrip("@").split("/")[-1].split("@")[0]
    else:
        bare = target.split("@")[0].split("==")[0].split(">=")[0].split("<=")[0].split(">")[0].split("<")[0]

    return bare.strip()


def detect(tool_output: str) -> dict:
    """Detect install/auth patterns in tool_output.

    Scans the full output string for any line matching a known install pattern.
    Returns on the first match (most specific patterns are tried first).

    Args:
        tool_output: Complete string output from a tool execution.

    Returns:
        On match:    {"matched": True, "pattern": <label>, "target": <name>, "kind": <kind>}
        On no match: {"matched": False}
    """
    if not tool_output:
        return {"matched": False}

    for line in tool_output.splitlines():
        for label, regex, kind, group_idx in _INSTALL_PATTERNS:
            m = regex.search(line)
            if m:
                raw_target = m.group(group_idx)
                target = _normalize_target(raw_target)
                if not target or target.startswith("-"):
                    # Artifact from regex: skip flags like '-g'
                    continue
                return {
                    "matched": True,
                    "pattern": label,
                    "target": target,
                    "kind": kind,
                }

    return {"matched": False}


def resolve_workspace(cwd: Optional[str] = None) -> str:
    """Return the current workspace identity.

    Pure delegation to gaia.project.current() -- no fallback logic
    implemented here. B0 owns the three-level fallback (git remote ->
    directory name -> literal "global").

    Args:
        cwd: Optional working directory. When None, gaia.project.current()
             uses Path.cwd() internally.

    Returns:
        Workspace identity string (never empty, never raises).
    """
    from gaia.project import current as _project_current
    if cwd is not None:
        return _project_current(cwd)
    return _project_current()


def _get_family(target: str) -> str:
    """Look up the family label for a target name.

    Args:
        target: Normalized package/tool name (e.g. "acli", "gcloud").

    Returns:
        Family label string (e.g. "atlassian", "google") or "generic" if
        not found in the mapping.
    """
    lower = target.lower()
    return _FAMILY_MAP.get(lower, "generic")


def build_topic_key(kind: str, target: str) -> str:
    """Build the topic_key for idempotent upsert in the integrations table.

    Format: "{kind}/{family}/{target}"

    Examples:
        build_topic_key("cli", "acli")   -> "cli/atlassian/acli"
        build_topic_key("cli", "gcloud") -> "cli/google/gcloud"
        build_topic_key("pkg", "pytest") -> "pkg/generic/pytest"

    Args:
        kind:   Install kind ("cli", "pkg", etc.)
        target: Normalized package/tool name.

    Returns:
        topic_key string for use in store.save_integration().
    """
    family = _get_family(target)
    return f"{kind}/{family}/{target.lower()}"
