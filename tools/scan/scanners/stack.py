"""
Stack Scanner

Detects project languages, frameworks, build tools, monorepo structure,
and project identity from manifest files and dependency declarations.

Owned sections: project_identity, stack
Contract: specs/002-gaia-scan/data-model.md sections 2.3, 2.4
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.scan.scanners.base import BaseScanner, ScanResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Language detection: mapping of manifest files to language names
# ---------------------------------------------------------------------------
LANGUAGE_MANIFESTS: List[Tuple[str, str]] = [
    # (filename_or_glob, language_name)
    ("package.json", "javascript"),
    ("pyproject.toml", "python"),
    ("setup.py", "python"),
    ("requirements.txt", "python"),
    ("go.mod", "go"),
    ("Cargo.toml", "rust"),
    ("pom.xml", "java"),
    ("build.gradle", "java"),
    ("build.gradle.kts", "java"),
    ("composer.json", "php"),
    ("Gemfile", "ruby"),
]

# C#/.NET uses glob patterns
CSHARP_EXTENSIONS = (".csproj", ".sln")

# ---------------------------------------------------------------------------
# Framework detection: mapping of dependency names to framework info
# ---------------------------------------------------------------------------
# (dep_name, framework_name, language)
JS_FRAMEWORKS: List[Tuple[str, str, str]] = [
    ("@nestjs/core", "nestjs", "javascript"),
    ("express", "express", "javascript"),
    ("react", "react", "javascript"),
    ("next", "next.js", "javascript"),
    ("@angular/core", "angular", "javascript"),
    ("vue", "vue", "javascript"),
    ("nuxt", "nuxt", "javascript"),
    ("svelte", "svelte", "javascript"),
    ("hono", "hono", "javascript"),
    ("fastify", "fastify", "javascript"),
    ("koa", "koa", "javascript"),
]

PYTHON_FRAMEWORKS: List[Tuple[str, str, str]] = [
    ("fastapi", "fastapi", "python"),
    ("flask", "flask", "python"),
    ("django", "django", "python"),
    ("starlette", "starlette", "python"),
    ("tornado", "tornado", "python"),
    ("sanic", "sanic", "python"),
    ("aiohttp", "aiohttp", "python"),
]

# ---------------------------------------------------------------------------
# Build tool / lock file detection
# ---------------------------------------------------------------------------
LOCK_FILE_TO_TOOL: List[Tuple[str, str]] = [
    ("package-lock.json", "npm"),
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("poetry.lock", "poetry"),
    ("Pipfile.lock", "pipenv"),
    ("Cargo.lock", "cargo"),
    ("go.sum", "go"),
    ("Gemfile.lock", "bundler"),
    ("composer.lock", "composer"),
]

MANIFEST_TO_BUILD_TOOL: List[Tuple[str, str]] = [
    ("Makefile", "make"),
    ("pom.xml", "maven"),
    ("build.gradle", "gradle"),
    ("build.gradle.kts", "gradle"),
]

# ---------------------------------------------------------------------------
# Monorepo detection
# ---------------------------------------------------------------------------
MONOREPO_TOOLS: Dict[str, str] = {
    "turbo.json": "turborepo",
    "nx.json": "nx",
    "lerna.json": "lerna",
}

# Maximum depth for monorepo subdirectory scanning
MONOREPO_SCAN_DEPTH = 3

# Directories to always skip during scanning
SKIP_DIRS = frozenset({
    "node_modules", ".git", "__pycache__", ".tox", ".venv",
    "venv", "dist", "build", ".next", ".nuxt", "target",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "vendor",
    ".terraform", ".terragrunt-cache",
})


class StackScanner(BaseScanner):
    """Detects project stack: languages, frameworks, build tools, and identity.

    Scans the project root and subdirectories (for monorepo support) to
    detect languages from manifest files, frameworks from dependency
    declarations, and build tools from lock files.

    Owned sections: project_identity, stack
    """

    @property
    def SCANNER_NAME(self) -> str:
        return "stack"

    @property
    def SCANNER_VERSION(self) -> str:
        return "1.1.0"

    @property
    def OWNED_SECTIONS(self) -> List[str]:
        return ["project_identity", "stack"]

    def scan(self, root: Path) -> ScanResult:
        """Scan the project at root and return project_identity and stack sections.

        Args:
            root: Absolute path to the project root directory.

        Returns:
            ScanResult with project_identity and stack sections.
        """
        start_ms = time.monotonic() * 1000
        warnings: List[str] = []

        try:
            languages = self._detect_languages(root, warnings)
            frameworks = self._detect_frameworks(root, languages, warnings)
            build_tools = self._detect_build_tools(root, warnings)
            project_identity = self._detect_project_identity(root, languages, warnings)

            sections: Dict[str, Any] = {
                "project_identity": project_identity,
                "stack": {
                    "languages": languages,
                    "frameworks": frameworks,
                    "build_tools": build_tools,
                },
            }
        except Exception as exc:
            logger.warning("Stack scanner failed: %s", exc)
            sections = {
                "project_identity": {
                    "name": root.name,
                    "type": "unknown",
                    "description": None,
                    "manifest_file": None,
                    "monorepo": {
                        "detected": False,
                        "tool": None,
                        "workspace_roots": [],
                    },
                },
                "stack": {
                    "languages": [],
                    "frameworks": [],
                    "build_tools": [],
                },
            }
            warnings.append(f"Stack scanner error: {exc}")

        elapsed_ms = (time.monotonic() * 1000) - start_ms
        return self.make_result(sections, warnings=warnings, duration_ms=elapsed_ms)

    # ------------------------------------------------------------------
    # Language detection
    # ------------------------------------------------------------------

    def _detect_languages(
        self, root: Path, warnings: List[str]
    ) -> List[Dict[str, Any]]:
        """Detect programming languages from manifest files.

        Scans root directory and subdirectories for language-specific
        manifest files. Handles monorepo by scanning subdirs up to
        MONOREPO_SCAN_DEPTH.
        """
        seen_languages: Dict[str, Dict[str, Any]] = {}
        first_found = True

        # Scan root and subdirectories
        for manifest_file, language in LANGUAGE_MANIFESTS:
            for path in self._find_files(root, manifest_file):
                rel_path = str(path.relative_to(root))
                if language not in seen_languages:
                    seen_languages[language] = {
                        "name": language,
                        "manifest": rel_path,
                        "primary": first_found,
                    }
                    first_found = False

        # C#/.NET detection via glob patterns
        for ext in CSHARP_EXTENSIONS:
            for path in self._find_files_by_extension(root, ext):
                if "csharp" not in seen_languages:
                    rel_path = str(path.relative_to(root))
                    seen_languages["csharp"] = {
                        "name": "csharp",
                        "manifest": rel_path,
                        "primary": first_found,
                    }
                    first_found = False
                break  # Only need one match per extension type

        # Check for TypeScript: tsconfig*.json at root or subdirectories,
        # or .ts/.tsx file extensions in the project tree
        if "javascript" in seen_languages and self._has_typescript_indicators(root):
            js_entry = seen_languages.pop("javascript")
            seen_languages["typescript"] = {
                "name": "typescript",
                "manifest": js_entry["manifest"],
                "primary": js_entry["primary"],
            }

        return list(seen_languages.values())

    def _has_typescript_indicators(self, root: Path) -> bool:
        """Check for TypeScript indicators: tsconfig files or .ts/.tsx extensions.

        Searches root and subdirectories (for monorepo support).
        """
        # Check for tsconfig*.json at root
        for f in root.iterdir() if root.is_dir() else []:
            if f.is_file() and f.name.startswith("tsconfig") and f.name.endswith(".json"):
                return True

        # Check subdirectories for tsconfig*.json (monorepo workspace roots)
        for path in self._find_files(root, "tsconfig.json"):
            return True

        # Also check for tsconfig.*.json patterns in subdirectories
        try:
            for entry in self._iter_subdirs(root, depth=0):
                for f in entry.iterdir():
                    if f.is_file() and f.name.startswith("tsconfig") and f.name.endswith(".json"):
                        return True
        except (PermissionError, OSError):
            pass

        # Check for .ts or .tsx file extensions
        for ext in (".ts", ".tsx"):
            if self._find_files_by_extension(root, ext):
                return True

        return False

    def _iter_subdirs(self, root: Path, depth: int) -> List[Path]:
        """Iterate subdirectories respecting MONOREPO_SCAN_DEPTH and SKIP_DIRS."""
        if depth >= MONOREPO_SCAN_DEPTH:
            return []
        results: List[Path] = []
        try:
            for entry in sorted(root.iterdir()):
                if entry.is_dir() and entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                    results.append(entry)
                    results.extend(self._iter_subdirs(entry, depth + 1))
        except PermissionError:
            pass
        return results

    # ------------------------------------------------------------------
    # Framework detection
    # ------------------------------------------------------------------

    def _detect_frameworks(
        self,
        root: Path,
        languages: List[Dict[str, Any]],
        warnings: List[str],
    ) -> List[Dict[str, Any]]:
        """Detect frameworks from dependency declarations."""
        frameworks: List[Dict[str, Any]] = []
        lang_names = {lang["name"] for lang in languages}

        # JavaScript/TypeScript frameworks from package.json
        if "javascript" in lang_names or "typescript" in lang_names:
            js_lang = "typescript" if "typescript" in lang_names else "javascript"
            for path in self._find_files(root, "package.json"):
                found = self._detect_js_frameworks(path, js_lang, warnings)
                for fw in found:
                    if not any(f["name"] == fw["name"] for f in frameworks):
                        frameworks.append(fw)

            # NestJS wraps Express/Fastify -- promote NestJS to primary position
            # and mark the underlying framework as secondary
            self._promote_meta_framework(frameworks, "nestjs", ["express", "fastify"])

        # Python frameworks from pyproject.toml and requirements.txt
        if "python" in lang_names:
            for path in self._find_files(root, "pyproject.toml"):
                found = self._detect_python_frameworks_pyproject(path, warnings)
                for fw in found:
                    if not any(f["name"] == fw["name"] for f in frameworks):
                        frameworks.append(fw)

            for path in self._find_files(root, "requirements.txt"):
                found = self._detect_python_frameworks_requirements(path, warnings)
                for fw in found:
                    if not any(f["name"] == fw["name"] for f in frameworks):
                        frameworks.append(fw)

            for path in self._find_files(root, "setup.py"):
                found = self._detect_python_frameworks_setup_py(path, warnings)
                for fw in found:
                    if not any(f["name"] == fw["name"] for f in frameworks):
                        frameworks.append(fw)

        return frameworks

    def _detect_js_frameworks(
        self, package_json_path: Path, language: str, warnings: List[str]
    ) -> List[Dict[str, Any]]:
        """Detect JavaScript/TypeScript frameworks from package.json."""
        frameworks: List[Dict[str, Any]] = []
        try:
            data = json.loads(package_json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            warnings.append(f"Cannot read {package_json_path}: {exc}")
            return frameworks

        # Merge dependencies and devDependencies
        deps: Dict[str, str] = {}
        deps.update(data.get("dependencies", {}))
        deps.update(data.get("devDependencies", {}))

        for dep_name, framework_name, _ in JS_FRAMEWORKS:
            if dep_name in deps:
                version = deps[dep_name]
                # Strip version prefix (^, ~, >=, etc.)
                version_clean = re.sub(r"^[\^~>=<]*", "", version) if version else None
                frameworks.append({
                    "name": framework_name,
                    "language": language,
                    "version": version_clean or None,
                })

        return frameworks

    def _promote_meta_framework(
        self,
        frameworks: List[Dict[str, Any]],
        meta_name: str,
        underlying_names: List[str],
    ) -> None:
        """Promote a meta-framework (e.g. NestJS) above its underlying frameworks.

        When a meta-framework like NestJS is detected alongside its underlying
        framework (Express), move it to the first position and mark the
        underlying framework as secondary (role='underlying').
        """
        meta_idx = None
        for i, fw in enumerate(frameworks):
            if fw["name"] == meta_name:
                meta_idx = i
                break

        if meta_idx is None:
            return

        # Move meta-framework to front
        if meta_idx > 0:
            meta_fw = frameworks.pop(meta_idx)
            frameworks.insert(0, meta_fw)

        # Mark underlying frameworks
        for fw in frameworks:
            if fw["name"] in underlying_names:
                fw["role"] = "underlying"

    def _detect_python_frameworks_pyproject(
        self, pyproject_path: Path, warnings: List[str]
    ) -> List[Dict[str, Any]]:
        """Detect Python frameworks from pyproject.toml dependencies."""
        frameworks: List[Dict[str, Any]] = []
        try:
            content = pyproject_path.read_text(encoding="utf-8")
        except OSError as exc:
            warnings.append(f"Cannot read {pyproject_path}: {exc}")
            return frameworks

        # Parse dependencies from [project.dependencies] and [tool.poetry.dependencies]
        # Using simple regex since we avoid external TOML parser dependency
        deps_text = self._extract_toml_deps(content)

        for dep_name, framework_name, lang in PYTHON_FRAMEWORKS:
            # Match dep_name with optional version specifier
            pattern = rf'(?:^|\n)\s*["\']?{re.escape(dep_name)}(?:\[.*?\])?["\']?\s*(?:[>=<~!]|$)'
            if re.search(pattern, deps_text, re.IGNORECASE):
                version = self._extract_python_version(deps_text, dep_name)
                frameworks.append({
                    "name": framework_name,
                    "language": lang,
                    "version": version,
                })

        return frameworks

    def _detect_python_frameworks_requirements(
        self, requirements_path: Path, warnings: List[str]
    ) -> List[Dict[str, Any]]:
        """Detect Python frameworks from requirements.txt."""
        frameworks: List[Dict[str, Any]] = []
        try:
            content = requirements_path.read_text(encoding="utf-8")
        except OSError as exc:
            warnings.append(f"Cannot read {requirements_path}: {exc}")
            return frameworks

        for dep_name, framework_name, lang in PYTHON_FRAMEWORKS:
            pattern = rf"(?:^|\n)\s*{re.escape(dep_name)}(?:\[.*?\])?\s*(?:([>=<~!]+)\s*([\d.]+))?"
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                version = match.group(2) if match.group(2) else None
                frameworks.append({
                    "name": framework_name,
                    "language": lang,
                    "version": version,
                })

        return frameworks

    def _detect_python_frameworks_setup_py(
        self, setup_py_path: Path, warnings: List[str]
    ) -> List[Dict[str, Any]]:
        """Detect Python frameworks from setup.py install_requires."""
        frameworks: List[Dict[str, Any]] = []
        try:
            content = setup_py_path.read_text(encoding="utf-8")
        except OSError as exc:
            warnings.append(f"Cannot read {setup_py_path}: {exc}")
            return frameworks

        for dep_name, framework_name, lang in PYTHON_FRAMEWORKS:
            pattern = rf'["\']({re.escape(dep_name)}(?:\[.*?\])?(?:\s*[>=<~!]+\s*[\d.]+)?)["\']'
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                version = self._extract_inline_version(match.group(1))
                frameworks.append({
                    "name": framework_name,
                    "language": lang,
                    "version": version,
                })

        return frameworks

    # ------------------------------------------------------------------
    # Build tool detection
    # ------------------------------------------------------------------

    def _detect_build_tools(
        self, root: Path, warnings: List[str]
    ) -> List[Dict[str, Any]]:
        """Detect build tools from lock files and manifest files."""
        tools: List[Dict[str, Any]] = []
        seen_tools: set = set()

        # Lock file detection
        for lock_file, tool_name in LOCK_FILE_TO_TOOL:
            for path in self._find_files(root, lock_file):
                if tool_name not in seen_tools:
                    rel_path = str(path.relative_to(root))
                    tools.append({
                        "name": tool_name,
                        "detected_by": "lock_file",
                        "lock_file": rel_path,
                    })
                    seen_tools.add(tool_name)

        # Manifest-based build tool detection
        for manifest_file, tool_name in MANIFEST_TO_BUILD_TOOL:
            for path in self._find_files(root, manifest_file):
                if tool_name not in seen_tools:
                    tools.append({
                        "name": tool_name,
                        "detected_by": "manifest",
                        "lock_file": None,
                    })
                    seen_tools.add(tool_name)

        # Detect pip from requirements.txt (no lock file equivalent)
        for path in self._find_files(root, "requirements.txt"):
            if "pip" not in seen_tools:
                tools.append({
                    "name": "pip",
                    "detected_by": "manifest",
                    "lock_file": None,
                })
                seen_tools.add("pip")

        # Detect poetry from pyproject.toml [tool.poetry] section
        if "poetry" not in seen_tools:
            for path in self._find_files(root, "pyproject.toml"):
                try:
                    content = path.read_text(encoding="utf-8")
                    if "[tool.poetry]" in content:
                        tools.append({
                            "name": "poetry",
                            "detected_by": "manifest",
                            "lock_file": None,
                        })
                        seen_tools.add("poetry")
                        break
                except OSError:
                    pass

        # Detect monorepo build orchestrators (turbo.json, nx.json, lerna.json)
        # at root and in subdirectories
        for config_file, tool_name in MONOREPO_TOOLS.items():
            if tool_name not in seen_tools:
                for path in self._find_files(root, config_file):
                    rel_path = str(path.relative_to(root))
                    tools.append({
                        "name": tool_name,
                        "detected_by": "config_file",
                        "lock_file": None,
                        "config_file": rel_path,
                    })
                    seen_tools.add(tool_name)
                    break  # One match per tool is enough

        return tools

    # ------------------------------------------------------------------
    # Project identity detection
    # ------------------------------------------------------------------

    def _detect_project_identity(
        self,
        root: Path,
        languages: List[Dict[str, Any]],
        warnings: List[str],
    ) -> Dict[str, Any]:
        """Detect project identity from manifest files.

        Reads name, description, and type from the primary manifest file
        (package.json or pyproject.toml). Falls back to directory name.
        """
        name: Optional[str] = None
        description: Optional[str] = None
        manifest_file: Optional[str] = None
        project_type = "unknown"

        # Try package.json first
        pkg_json = root / "package.json"
        if pkg_json.is_file():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8"))
                name = data.get("name")
                description = data.get("description")
                manifest_file = "package.json"

                # Detect monorepo indicators in package.json
                if data.get("workspaces"):
                    project_type = "monorepo"
                elif data.get("private") is True and not data.get("main"):
                    project_type = "monorepo"
                else:
                    project_type = "application"
            except (json.JSONDecodeError, OSError) as exc:
                warnings.append(f"Cannot read package.json: {exc}")

        # Try pyproject.toml
        pyproject = root / "pyproject.toml"
        if pyproject.is_file():
            try:
                content = pyproject.read_text(encoding="utf-8")
                if name is None:
                    name = self._extract_toml_value(content, "name")
                if description is None:
                    description = self._extract_toml_value(content, "description")
                if manifest_file is None:
                    manifest_file = "pyproject.toml"
                if project_type == "unknown":
                    project_type = "library" if "[build-system]" in content else "application"
            except OSError as exc:
                warnings.append(f"Cannot read pyproject.toml: {exc}")

        # Try go.mod
        go_mod = root / "go.mod"
        if go_mod.is_file() and name is None:
            try:
                content = go_mod.read_text(encoding="utf-8")
                match = re.search(r"^module\s+(\S+)", content, re.MULTILINE)
                if match:
                    name = match.group(1).split("/")[-1]
                    manifest_file = "go.mod"
                    if project_type == "unknown":
                        project_type = "application"
            except OSError as exc:
                warnings.append(f"Cannot read go.mod: {exc}")

        # Try Cargo.toml
        cargo_toml = root / "Cargo.toml"
        if cargo_toml.is_file() and name is None:
            try:
                content = cargo_toml.read_text(encoding="utf-8")
                name = self._extract_toml_value(content, "name")
                if description is None:
                    description = self._extract_toml_value(content, "description")
                manifest_file = "Cargo.toml"
                if project_type == "unknown":
                    # Cargo workspace = monorepo
                    if "[workspace]" in content:
                        project_type = "monorepo"
                    else:
                        project_type = "application"
            except OSError as exc:
                warnings.append(f"Cannot read Cargo.toml: {exc}")

        # Try composer.json
        composer = root / "composer.json"
        if composer.is_file() and name is None:
            try:
                data = json.loads(composer.read_text(encoding="utf-8"))
                name = data.get("name")
                description = data.get("description")
                manifest_file = "composer.json"
                if project_type == "unknown":
                    project_type = "application"
            except (json.JSONDecodeError, OSError) as exc:
                warnings.append(f"Cannot read composer.json: {exc}")

        # Before falling back to directory name, check monorepo subdirectory
        # package.json files for a project name
        if name is None:
            name = self._derive_name_from_subdirs(root, warnings)

        # Fallback to directory name
        if name is None:
            name = root.name

        # Monorepo detection
        monorepo_info = self._detect_monorepo(root, warnings)
        if monorepo_info["detected"]:
            project_type = "monorepo"

        # Multi-language implies potential monorepo
        if len(languages) > 1 and not monorepo_info["detected"]:
            # Check if languages come from different subdirectories
            subdirs = set()
            for lang in languages:
                manifest_dir = str(Path(lang["manifest"]).parent)
                if manifest_dir != ".":
                    subdirs.add(manifest_dir)
            if len(subdirs) > 1:
                project_type = "monorepo"
                monorepo_info["detected"] = True

        return {
            "name": name,
            "type": project_type,
            "description": description,
            "manifest_file": manifest_file,
            "monorepo": monorepo_info,
        }

    def _derive_name_from_subdirs(
        self, root: Path, warnings: List[str]
    ) -> Optional[str]:
        """Derive project name from package.json in immediate subdirectories.

        Checks subdirectories that look like monorepo roots (contain
        package.json with a name field) and returns the first name found.
        """
        try:
            for entry in sorted(root.iterdir()):
                if not entry.is_dir():
                    continue
                if entry.name in SKIP_DIRS or entry.name.startswith("."):
                    continue
                sub_pkg = entry / "package.json"
                if sub_pkg.is_file():
                    try:
                        data = json.loads(sub_pkg.read_text(encoding="utf-8"))
                        pkg_name = data.get("name")
                        if pkg_name and isinstance(pkg_name, str):
                            return pkg_name
                    except (json.JSONDecodeError, OSError):
                        continue
        except PermissionError:
            pass
        return None

    # ------------------------------------------------------------------
    # Monorepo detection
    # ------------------------------------------------------------------

    def _detect_monorepo(
        self, root: Path, warnings: List[str]
    ) -> Dict[str, Any]:
        """Detect monorepo tool and workspace roots."""
        result: Dict[str, Any] = {
            "detected": False,
            "tool": None,
            "workspace_roots": [],
        }

        # Check for monorepo tool config files at root level
        for config_file, tool_name in MONOREPO_TOOLS.items():
            if (root / config_file).is_file():
                result["detected"] = True
                result["tool"] = tool_name
                break

        # Check pnpm workspaces at root level
        pnpm_workspace = root / "pnpm-workspace.yaml"
        if pnpm_workspace.is_file():
            result["detected"] = True
            if result["tool"] is None:
                result["tool"] = "pnpm-workspaces"

        # Check npm/yarn workspaces in package.json at root level
        pkg_json = root / "package.json"
        if pkg_json.is_file():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8"))
                workspaces = data.get("workspaces")
                if workspaces:
                    result["detected"] = True
                    if result["tool"] is None:
                        result["tool"] = "npm-workspaces"
                    # Extract workspace patterns
                    if isinstance(workspaces, list):
                        workspace_patterns = workspaces
                    elif isinstance(workspaces, dict):
                        workspace_patterns = workspaces.get("packages", [])
                    else:
                        workspace_patterns = []
                    # Resolve workspace roots from patterns
                    for pattern in workspace_patterns:
                        result["workspace_roots"].append(str(pattern))
            except (json.JSONDecodeError, OSError):
                pass

        # If not yet detected, scan immediate subdirectories for workspace
        # config files (handles projects where monorepo root is a subdirectory)
        if not result["detected"]:
            result = self._detect_monorepo_in_subdirs(root, result, warnings)

        return result

    def _detect_monorepo_in_subdirs(
        self,
        root: Path,
        result: Dict[str, Any],
        warnings: List[str],
    ) -> Dict[str, Any]:
        """Scan immediate subdirectories for monorepo workspace config files.

        Detects monorepo when a subdirectory contains workspace config files
        like pnpm-workspace.yaml, pnpm-lock.yaml, lerna.json, etc.
        """
        # Workspace config files that indicate a monorepo root
        workspace_markers = [
            ("pnpm-workspace.yaml", "pnpm-workspaces"),
            ("pnpm-lock.yaml", "pnpm-workspaces"),
            ("lerna.json", "lerna"),
            ("turbo.json", "turborepo"),
            ("nx.json", "nx"),
        ]

        try:
            for entry in sorted(root.iterdir()):
                if not entry.is_dir():
                    continue
                if entry.name in SKIP_DIRS or entry.name.startswith("."):
                    continue

                for marker_file, tool_name in workspace_markers:
                    if (entry / marker_file).is_file():
                        result["detected"] = True
                        if result["tool"] is None:
                            result["tool"] = tool_name
                        subdir_rel = str(entry.relative_to(root))
                        if subdir_rel not in result["workspace_roots"]:
                            result["workspace_roots"].append(subdir_rel)
                        break

                # Also check for package.json with workspaces in subdirs
                sub_pkg = entry / "package.json"
                if sub_pkg.is_file() and not result["detected"]:
                    try:
                        data = json.loads(sub_pkg.read_text(encoding="utf-8"))
                        if data.get("workspaces"):
                            result["detected"] = True
                            if result["tool"] is None:
                                result["tool"] = "npm-workspaces"
                            subdir_rel = str(entry.relative_to(root))
                            if subdir_rel not in result["workspace_roots"]:
                                result["workspace_roots"].append(subdir_rel)
                    except (json.JSONDecodeError, OSError):
                        pass

                if result["detected"]:
                    break
        except PermissionError:
            pass

        return result

    # ------------------------------------------------------------------
    # File search helpers
    # ------------------------------------------------------------------

    def _find_files(self, root: Path, filename: str) -> List[Path]:
        """Find files matching filename in root and subdirectories.

        Respects MONOREPO_SCAN_DEPTH and SKIP_DIRS. Returns root-level
        matches first, then subdirectory matches.
        """
        results: List[Path] = []

        # Check root first
        root_file = root / filename
        if root_file.is_file():
            results.append(root_file)

        # Scan subdirectories up to MONOREPO_SCAN_DEPTH
        self._find_files_recursive(root, filename, results, 0)

        return results

    def _find_files_recursive(
        self, directory: Path, filename: str, results: List[Path], depth: int
    ) -> None:
        """Recursively search for files, respecting depth and skip dirs."""
        if depth >= MONOREPO_SCAN_DEPTH:
            return

        try:
            for entry in sorted(directory.iterdir()):
                if not entry.is_dir():
                    continue
                if entry.name in SKIP_DIRS or entry.name.startswith("."):
                    continue

                target = entry / filename
                if target.is_file():
                    results.append(target)

                self._find_files_recursive(entry, filename, results, depth + 1)
        except PermissionError:
            pass

    def _find_files_by_extension(self, root: Path, ext: str) -> List[Path]:
        """Find files with a given extension in root and subdirectories."""
        results: List[Path] = []

        # Check root
        try:
            for entry in root.iterdir():
                if entry.is_file() and entry.name.endswith(ext):
                    results.append(entry)
                    return results  # One match is enough
        except PermissionError:
            pass

        # Check subdirectories
        self._find_ext_recursive(root, ext, results, 0)
        return results

    def _find_ext_recursive(
        self, directory: Path, ext: str, results: List[Path], depth: int
    ) -> None:
        """Recursively search for files by extension."""
        if depth >= MONOREPO_SCAN_DEPTH or results:
            return

        try:
            for entry in sorted(directory.iterdir()):
                if entry.is_dir() and entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                    # Check for matching files in this directory
                    try:
                        for child in entry.iterdir():
                            if child.is_file() and child.name.endswith(ext):
                                results.append(child)
                                return
                    except PermissionError:
                        pass
                    self._find_ext_recursive(entry, ext, results, depth + 1)
        except PermissionError:
            pass

    # ------------------------------------------------------------------
    # TOML parsing helpers (no external dependency)
    # ------------------------------------------------------------------

    def _extract_toml_value(self, content: str, key: str) -> Optional[str]:
        """Extract a simple string value from TOML content.

        Handles: key = "value" patterns. Does NOT handle nested tables
        or multiline values -- for those, we use section-specific parsers.
        """
        pattern = rf'^\s*{re.escape(key)}\s*=\s*["\']([^"\']*)["\']'
        match = re.search(pattern, content, re.MULTILINE)
        return match.group(1) if match else None

    def _extract_toml_deps(self, content: str) -> str:
        """Extract dependency-related sections from pyproject.toml content.

        Returns a combined string of all dependency declarations for
        framework matching.
        """
        sections: List[str] = []

        # [project.dependencies]
        dep_match = re.search(
            r"\[project\]\s*\n(.*?)(?=\n\[|\Z)",
            content,
            re.DOTALL,
        )
        if dep_match:
            sections.append(dep_match.group(1))

        # dependencies = [...] array
        dep_array = re.search(
            r"dependencies\s*=\s*\[(.*?)\]",
            content,
            re.DOTALL,
        )
        if dep_array:
            sections.append(dep_array.group(1))

        # [tool.poetry.dependencies]
        poetry_deps = re.search(
            r"\[tool\.poetry\.dependencies\]\s*\n(.*?)(?=\n\[|\Z)",
            content,
            re.DOTALL,
        )
        if poetry_deps:
            sections.append(poetry_deps.group(1))

        # optional-dependencies sections
        opt_deps = re.findall(
            r"\[(?:project\.)?optional-dependencies(?:\.\w+)?\]\s*\n(.*?)(?=\n\[|\Z)",
            content,
            re.DOTALL,
        )
        sections.extend(opt_deps)

        return "\n".join(sections)

    def _extract_python_version(self, deps_text: str, dep_name: str) -> Optional[str]:
        """Extract version specifier for a Python dependency."""
        pattern = rf'["\']?{re.escape(dep_name)}(?:\[.*?\])?\s*[>=<~!]+\s*([\d.]+)'
        match = re.search(pattern, deps_text, re.IGNORECASE)
        return match.group(1) if match else None

    def _extract_inline_version(self, dep_string: str) -> Optional[str]:
        """Extract version from an inline dependency string like 'fastapi>=0.100.0'."""
        match = re.search(r"[>=<~!]+\s*([\d.]+)", dep_string)
        return match.group(1) if match else None
