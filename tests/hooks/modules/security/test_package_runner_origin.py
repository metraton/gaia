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
            "start": "vite",
            "test": "kubectl apply -f k8s/",
            "build": "tsc -p .",
            "postbuild": "kubectl apply -f k8s/",
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
    ("npm test", SIGNED),
    ("npm t", SIGNED),
    ("npm start", UNSIGNED),
    ("bun live", SIGNED),
    ("bun deploy", UNSIGNED),
    # `build` is harmless, but npm runs `postbuild` right after it.
    ("npm run build", SIGNED),
    ("yarn build", SIGNED),
    # 2. Runners: unsigned only when the binary resolves from the project's
    #    installed node_modules; being declared is not running from the project.
    ("npx eslint .", UNSIGNED),
    ("npx cowsay hello", SIGNED),
    ("npx prettier --check .", SIGNED),
    ("bunx eslint .", UNSIGNED),
    ("bunx cowsay hello", SIGNED),
    ("bun x eslint .", UNSIGNED),
    ("bun x cowsay hello", SIGNED),
    ("npm x eslint .", UNSIGNED),
    ("npm x cowsay hello", SIGNED),
    ("npm exec eslint .", UNSIGNED),
    ("npm exec cowsay hello", SIGNED),
    ("pnpm exec eslint .", UNSIGNED),
    ("pnpm exec cowsay hello", SIGNED),
    ("bun exec eslint .", UNSIGNED),
    ("bun exec cowsay hello", SIGNED),
    # Runners that always fetch from a registry, declared or not.
    ("pnpm dlx prettier --check .", SIGNED),
    ("pnpm dlx cowsay hello", SIGNED),
    ("yarn dlx prettier --check .", SIGNED),
    ("yarn dlx cowsay hello", SIGNED),
    ("pipx run ruff check .", SIGNED),
    ("pipx run cowsay hello", SIGNED),
    ("uvx ruff check .", SIGNED),
    ("uvx pycowsay hello", SIGNED),
    ("npx eslint@9 .", SIGNED),
    # `user/repo` is a GitHub fetch, not a path.
    ("npx user/repo", SIGNED),
    ("pnpm dlx user/repo", SIGNED),
    ("npm exec user/repo", SIGNED),
    # An option's separate value is never the package.
    ("npx --cache /tmp/c cowsay", SIGNED),
    ("npx --prefix /tmp/p cowsay", SIGNED),
    ("npm exec --globalconfig /tmp/g cowsay", SIGNED),
    # An option the runner tables do not know may take the next token as its
    # value, so the package cannot be told apart: fail closed.
    ("pnpm --store-dir /x dlx cowsay", SIGNED),
    ("bunx --some-opt ./x cowsay", SIGNED),
    ("npx --some-opt /tmp/x cowsay", SIGNED),
    ("npx --some-flag eslint .", SIGNED),
    ("npx -y eslint .", UNSIGNED),
    # The runner looks for the binary under its own directory option, not the
    # cwd where eslint happens to be installed.
    ("npx --prefix /nonexistent/p eslint .", SIGNED),
    ("npm -C /nonexistent/p exec eslint .", SIGNED),
    ("bun --cwd /nonexistent/p x eslint .", SIGNED),
    ("npx --prefix . eslint .", UNSIGNED),
    # `uv run` runs in the project, but `--with` fetches from PyPI.
    ("uv run --with requests pytest", SIGNED),
    ("uv run --with ./vendor/lib pytest", UNSIGNED),
    ("uv run pytest", UNSIGNED),
    ("npm exec --yes eslint .", UNSIGNED),
    # `npm cit` is `npm ci` followed by the test script, whose body mutates here.
    ("npm cit", SIGNED),
    ("npm install-ci-test", SIGNED),
    ("npx --package cowsay eslint .", SIGNED),
    ("uvx --from cowsay ruff", SIGNED),
    # 3. Adding a dependency.
    ("npm i left-pad", SIGNED),
    ("npm install left-pad", SIGNED),
    ("npm add left-pad", SIGNED),
    ("bun add left-pad", SIGNED),
    ("pnpm add left-pad", SIGNED),
    ("yarn add left-pad", SIGNED),
    ("npm it left-pad", SIGNED),
    ("npm install-test left-pad", SIGNED),
    # 4. Frozen install from the lockfile versus an install that may rewrite it.
    ("npm ci", UNSIGNED),
    ("bun install --frozen-lockfile", UNSIGNED),
    ("pnpm install --frozen-lockfile", UNSIGNED),
    ("yarn install --immutable", UNSIGNED),
    ("pnpm install --frozen-lockfile=true", UNSIGNED),
    ("pnpm install --frozen-lockfile=false", SIGNED),
    ("bun install --frozen-lockfile=false", SIGNED),
    ("yarn install --immutable=false", SIGNED),
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


def test_frozen_install_runs_the_project_lifecycle_scripts(project):
    manifest = json.loads((project / "package.json").read_text())
    manifest["scripts"]["postinstall"] = "kubectl apply -f k8s/"
    (project / "package.json").write_text(json.dumps(manifest))
    for command in ("npm ci", "pnpm install --frozen-lockfile"):
        assert detect_mutative_command(command, cwd=str(project)).is_mutative is True


def test_frozen_install_without_a_manifest_is_signed(tmp_path):
    for command in ("npm ci", "npm cit"):
        assert detect_mutative_command(command, cwd=str(tmp_path)).is_mutative is True


@pytest.mark.parametrize("scripts,signed", [
    ({"test": "vitest run", "postinstall": "kubectl apply -f k8s/"}, SIGNED),
    ({"test": "vitest run"}, UNSIGNED),
], ids=["mutating-postinstall", "harmless"])
def test_install_ci_test_is_a_frozen_install(tmp_path, scripts, signed):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": scripts}))
    assert detect_mutative_command("npm cit", cwd=str(tmp_path)).is_mutative is signed
