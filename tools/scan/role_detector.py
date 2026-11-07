"""
Role Detector

Classifies a repository into one of five roles based on the presence of
marker files and directories. This is a pure function module: no side
effects, no network calls, no DB writes. The callers (classify.py,
store_populator.py) persist the result.

Roles:
    'iac'         -- Terraform / Terragrunt / Pulumi / CDK
    'gitops'      -- Kustomize / Flux / ArgoCD / Helm charts
    'application' -- Service application (package.json, pyproject.toml,
                     Dockerfile, src/) without IaC or GitOps markers
    'monorepo'    -- Mix of the above or explicit monorepo config
    'service'     -- Subset of application with service-runtime markers

Detection priority (first match in _classify wins):
  1. monorepo  -- pnpm-workspace.yaml | lerna.json | nx.json | markers of at
                  least two of iac, gitops and application | a root
                  package.json plus package.json in two or more sub-dirs
  2. iac       -- *.tf (root, or under modules/ or live/) | terragrunt.hcl
                  | Pulumi.yaml
  3. gitops    -- kustomization.yaml | Chart.yaml | flux-system/ dir
  4. service   -- application markers AND a service-runtime/ subdir
  5. application -- package.json | pyproject.toml | Dockerfile | setup.py |
                  requirements.txt | go.mod | Cargo.toml | src/ (no *.tf)
  6. iac        -- fallback when only modules/ or live/ present
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

Role = Literal["application", "iac", "gitops", "service", "monorepo"]


def detect_role(repo_path: Path) -> Role:
    """Classify a repository directory into one of five roles.

    Args:
        repo_path: Absolute path to the repository root.

    Returns:
        One of 'application', 'iac', 'gitops', 'service', 'monorepo'.
        Falls back to 'application' when no specific markers are found.

    Raises:
        Nothing -- all errors are swallowed and 'application' is returned.
    """
    try:
        return _classify(repo_path)
    except Exception:
        return "application"


def _classify(root: Path) -> Role:
    flags = _detect_flags(root)

    if flags["is_monorepo"]:
        return "monorepo"

    if flags["has_tf"] or flags["has_terragrunt"] or flags["has_pulumi"]:
        return "iac"

    if flags["has_kustomize"] or flags["has_helm"] or flags["has_flux_system"]:
        return "gitops"

    if flags["has_app"] and flags["has_service_runtime"]:
        return "service"

    if flags["has_app"]:
        return "application"

    if flags["has_iac_dir"]:
        return "iac"

    return "application"


def _detect_flags(root: Path) -> dict:
    flags = {
        "has_tf": False,
        "has_terragrunt": False,
        "has_pulumi": False,
        "has_kustomize": False,
        "has_helm": False,
        "has_flux_system": False,
        "has_app": False,
        "has_service_runtime": False,
        "has_iac_dir": False,
        "is_monorepo": False,
    }

    if not root.is_dir():
        return flags

    try:
        children = list(root.iterdir())
    except OSError:
        return flags

    child_names = {c.name for c in children}
    child_dirs = {c.name for c in children if c.is_dir()}
    child_files = {c.name for c in children if c.is_file()}

    if any(f.endswith(".tf") for f in child_files):
        flags["has_tf"] = True
    if "terragrunt.hcl" in child_files:
        flags["has_terragrunt"] = True
    if "Pulumi.yaml" in child_files:
        flags["has_pulumi"] = True

    if "modules" in child_dirs or "live" in child_dirs:
        flags["has_iac_dir"] = True
        # *.tf files under modules/ or live/ make this an iac repo outright
        if not flags["has_tf"]:
            for subdir in ("modules", "live"):
                if (root / subdir).is_dir():
                    for p in (root / subdir).rglob("*.tf"):
                        flags["has_tf"] = True
                        break

    if "kustomization.yaml" in child_files or "kustomization.yml" in child_files:
        flags["has_kustomize"] = True
    if "Chart.yaml" in child_files:
        flags["has_helm"] = True
    if "flux-system" in child_dirs:
        flags["has_flux_system"] = True

    app_markers = {"package.json", "pyproject.toml", "Dockerfile", "setup.py",
                   "requirements.txt", "go.mod", "Cargo.toml"}
    if app_markers & child_files:
        flags["has_app"] = True
    if "src" in child_dirs and not flags["has_tf"]:
        flags["has_app"] = True

    if "service-runtime" in child_dirs:
        flags["has_service_runtime"] = True

    if _is_monorepo(root, child_files, child_dirs, flags):
        flags["is_monorepo"] = True

    return flags


def _is_monorepo(
    root: Path,
    child_files: set,
    child_dirs: set,
    flags: dict,
) -> bool:
    """Return True when monorepo patterns are detected."""
    if "pnpm-workspace.yaml" in child_files:
        return True
    if "lerna.json" in child_files:
        return True
    if "nx.json" in child_files:
        return True

    role_types_present = sum([
        flags["has_tf"] or flags["has_terragrunt"] or flags["has_pulumi"] or flags["has_iac_dir"],
        flags["has_kustomize"] or flags["has_helm"] or flags["has_flux_system"],
        flags["has_app"],
    ])
    if role_types_present >= 2:
        return True

    if "package.json" in child_files:
        sub_pkg = sum(
            1 for d in child_dirs
            if (root / d / "package.json").is_file()
        )
        if sub_pkg >= 2:
            return True

    return False
