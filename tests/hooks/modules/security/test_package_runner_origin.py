"""Package runners are signed by where the code comes from, not by the command name.

A script already in the project is signed only when its body changes something
live; a runner is signed when its package would come from a registry; adding a
dependency is signed; a frozen install from the lockfile is not.
"""
import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402

SIGNED = True
UNSIGNED = False


@pytest.fixture
def project(tmp_path):
    """A project whose script names say the opposite of what their bodies do,
    a local bin (eslint), declared node and python dependencies, and a
    harmless prepare lifecycle script."""
    (tmp_path / "package.json").write_text(json.dumps({
        "scripts": {
            "deploy": "vite build",
            "live": "kubectl apply -f k8s/",
            "prepare": "tsc -p .",
        },
        "dependencies": {"prettier": "^3.0.0", "prisma": "^5.0.0"},
    }))
    bin_dir = tmp_path / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "eslint").write_text("#!/usr/bin/env node\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
        'dependencies = ["ruff>=0.5"]\n'
    )
    return tmp_path


CASES = [
    # 1. A script already in the project: signed only by what its body does.
    ("npm run deploy", UNSIGNED),
    ("bun run deploy", UNSIGNED),
    ("pnpm run deploy", UNSIGNED),
    ("yarn run deploy", UNSIGNED),
    ("yarn deploy", UNSIGNED),
    ("npm run live", SIGNED),
    ("bun run live", SIGNED),
    ("pnpm run live", SIGNED),
    ("yarn run live", SIGNED),
    ("yarn live", SIGNED),
    ("pnpm live", SIGNED),
    ("bun run missing-script", SIGNED),
    # `pnpm deploy` is a pnpm command, not the script of that name.
    ("pnpm deploy", SIGNED),
    # 2. Runners: unsigned when the package resolves in the project, signed
    #    when it would be fetched, signed when that cannot be told.
    ("npx eslint .", UNSIGNED),
    ("npx cowsay hello", SIGNED),
    ("bunx eslint .", UNSIGNED),
    ("bunx cowsay hello", SIGNED),
    ("bun x eslint .", UNSIGNED),
    ("bun x cowsay hello", SIGNED),
    ("pnpm dlx prettier --check .", UNSIGNED),
    ("pnpm dlx cowsay hello", SIGNED),
    ("yarn dlx prettier --check .", UNSIGNED),
    ("yarn dlx cowsay hello", SIGNED),
    ("pipx run ruff check .", UNSIGNED),
    ("pipx run cowsay hello", SIGNED),
    ("uvx ruff check .", UNSIGNED),
    ("uvx pycowsay hello", SIGNED),
    ("npx eslint@9 .", SIGNED),
    ("npx --package cowsay eslint .", SIGNED),
    ("uvx --from cowsay ruff", SIGNED),
    # 3. Adding a dependency.
    ("npm i left-pad", SIGNED),
    ("npm install left-pad", SIGNED),
    ("npm add left-pad", SIGNED),
    ("bun add left-pad", SIGNED),
    ("pnpm add left-pad", SIGNED),
    ("yarn add left-pad", SIGNED),
    # 4. Frozen install from the lockfile versus an install that may rewrite it.
    ("npm ci", UNSIGNED),
    ("bun install --frozen-lockfile", UNSIGNED),
    ("pnpm install --frozen-lockfile", UNSIGNED),
    ("yarn install --immutable", UNSIGNED),
    ("npm i", SIGNED),
    ("npm install", SIGNED),
    ("bun install", SIGNED),
    ("pnpm install", SIGNED),
    ("yarn install", SIGNED),
    ("yarn", SIGNED),
    # 5. A runner whose package is local still answers for what it runs.
    ("npx prisma migrate deploy", SIGNED),
]


@pytest.mark.parametrize("command,signed", CASES, ids=[c for c, _ in CASES])
def test_signature_follows_code_origin(project, command, signed):
    assert detect_mutative_command(command, cwd=str(project)).is_mutative is signed


def test_npm_i_and_npm_install_classify_identically(project):
    for args in ("left-pad", "", "--dry-run left-pad"):
        alias = detect_mutative_command(f"npm i {args}", cwd=str(project))
        full = detect_mutative_command(f"npm install {args}", cwd=str(project))
        assert (alias.is_mutative, alias.verb) == (full.is_mutative, full.verb)


def test_runner_outside_any_project_is_signed(tmp_path):
    assert detect_mutative_command("npx eslint .", cwd=str(tmp_path)).is_mutative is True


def test_malformed_python_manifest_counts_as_undeclared(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = "ruff"\n[dependency-groups]\ndev = 3\n'
    )
    assert detect_mutative_command("uvx ruff check .", cwd=str(tmp_path)).is_mutative is True


def test_frozen_install_runs_the_project_lifecycle_scripts(project):
    manifest = json.loads((project / "package.json").read_text())
    manifest["scripts"]["postinstall"] = "kubectl apply -f k8s/"
    (project / "package.json").write_text(json.dumps(manifest))
    for command in ("npm ci", "pnpm install --frozen-lockfile"):
        assert detect_mutative_command(command, cwd=str(project)).is_mutative is True


def test_frozen_install_without_a_manifest_is_signed(tmp_path):
    assert detect_mutative_command("npm ci", cwd=str(tmp_path)).is_mutative is True
