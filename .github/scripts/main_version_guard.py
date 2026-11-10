#!/usr/bin/env python3
"""Fail when a pre-release version is declared in a tree that is headed for `main`.

`main` is the marketplace's default channel (`source: "."`, no ref), so whatever
version it declares reaches every plugin user who pins nothing. A semver
pre-release (any hyphen suffix, such as 5.7.2-rc.1) belongs on a release branch.

Exit status: 0 every declared version is stable, 1 a pre-release was found.
"""

import argparse
import json
import sys
import tomllib
from pathlib import Path


def declared_versions(root):
    """Map each version source under *root* to the version it declares."""
    root = Path(root)
    versions = {}
    for name in ("package.json", ".claude-plugin/plugin.json"):
        versions[name] = json.loads((root / name).read_text(encoding="utf-8"))["version"]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    versions["pyproject.toml"] = pyproject["project"]["version"]
    return versions


def prereleases(versions):
    """The subset of *versions* with a pre-release suffix."""
    return {name: version for name, version in versions.items() if "-" in version}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="checkout to read (default: the current directory)")
    args = parser.parse_args(argv)

    found = prereleases(declared_versions(args.root))
    if not found:
        print("No pre-release version is declared; this tree may reach main.")
        return 0
    for name, version in sorted(found.items()):
        print(f"{name} declares {version}, a pre-release", file=sys.stderr)
    print(
        "main serves its version to every plugin user who pins nothing. "
        "Publish the pre-release from a release/* branch and land only the stable version here.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
