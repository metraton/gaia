"""
Unit tests for the Stack Scanner (T020).

Tests language detection, framework detection, build tool detection,
monorepo detection, and project_identity extraction.
"""

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from tools.scan.scanners.stack import StackScanner


@pytest.fixture
def scanner() -> StackScanner:
    """Create a StackScanner instance."""
    return StackScanner()


# ---------------------------------------------------------------------------
# Scanner basics
# ---------------------------------------------------------------------------


class TestStackScannerBasics:
    """Test scanner metadata and basic contract."""

    def test_scanner_name(self, scanner: StackScanner) -> None:
        assert scanner.SCANNER_NAME == "stack"

    def test_scanner_version(self, scanner: StackScanner) -> None:
        assert scanner.SCANNER_VERSION == "1.0.0"

    def test_owned_sections(self, scanner: StackScanner) -> None:
        assert "project_identity" in scanner.OWNED_SECTIONS
        assert "stack" in scanner.OWNED_SECTIONS

    def test_source_tag(self, scanner: StackScanner) -> None:
        assert scanner.source_tag == "scanner:stack"


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


class TestLanguageDetection:
    """Test language detection from manifest files."""

    def test_detect_nodejs_from_package_json(
        self, scanner: StackScanner, node_project: Path
    ) -> None:
        result = scanner.scan(node_project)
        languages = result.sections["stack"]["languages"]
        lang_names = [lang["name"] for lang in languages]
        assert "javascript" in lang_names

    def test_detect_typescript_with_tsconfig(
        self, scanner: StackScanner, node_project: Path
    ) -> None:
        (node_project / "tsconfig.json").write_text("{}")
        result = scanner.scan(node_project)
        languages = result.sections["stack"]["languages"]
        lang_names = [lang["name"] for lang in languages]
        assert "typescript" in lang_names
        assert "javascript" not in lang_names

    def test_detect_python_from_pyproject_toml(
        self, scanner: StackScanner, python_project: Path
    ) -> None:
        result = scanner.scan(python_project)
        languages = result.sections["stack"]["languages"]
        lang_names = [lang["name"] for lang in languages]
        assert "python" in lang_names

    def test_detect_python_from_setup_py(
        self, scanner: StackScanner, tmp_path: Path
    ) -> None:
        (tmp_path / "setup.py").write_text(
            'from setuptools import setup\nsetup(name="test")\n'
        )
        result = scanner.scan(tmp_path)
        languages = result.sections["stack"]["languages"]
        lang_names = [lang["name"] for lang in languages]
        assert "python" in lang_names

    def test_detect_python_from_requirements_txt(
        self, scanner: StackScanner, tmp_path: Path
    ) -> None:
        (tmp_path / "requirements.txt").write_text("flask>=2.0\n")
        result = scanner.scan(tmp_path)
        languages = result.sections["stack"]["languages"]
        lang_names = [lang["name"] for lang in languages]
        assert "python" in lang_names

    def test_detect_go_from_go_mod(
        self, scanner: StackScanner, go_project: Path
    ) -> None:
        result = scanner.scan(go_project)
        languages = result.sections["stack"]["languages"]
        lang_names = [lang["name"] for lang in languages]
        assert "go" in lang_names

    def test_detect_rust_from_cargo_toml(
        self, scanner: StackScanner, rust_project: Path
    ) -> None:
        result = scanner.scan(rust_project)
        languages = result.sections["stack"]["languages"]
        lang_names = [lang["name"] for lang in languages]
        assert "rust" in lang_names

    def test_detect_java_from_pom_xml(
        self, scanner: StackScanner, java_maven_project: Path
    ) -> None:
        result = scanner.scan(java_maven_project)
        languages = result.sections["stack"]["languages"]
        lang_names = [lang["name"] for lang in languages]
        assert "java" in lang_names

    def test_detect_java_from_build_gradle(
        self, scanner: StackScanner, java_gradle_project: Path
    ) -> None:
        result = scanner.scan(java_gradle_project)
        languages = result.sections["stack"]["languages"]
        lang_names = [lang["name"] for lang in languages]
        assert "java" in lang_names

    def test_detect_php_from_composer_json(
        self, scanner: StackScanner, php_project: Path
    ) -> None:
        result = scanner.scan(php_project)
        languages = result.sections["stack"]["languages"]
        lang_names = [lang["name"] for lang in languages]
        assert "php" in lang_names

    def test_detect_ruby_from_gemfile(
        self, scanner: StackScanner, ruby_project: Path
    ) -> None:
        result = scanner.scan(ruby_project)
        languages = result.sections["stack"]["languages"]
        lang_names = [lang["name"] for lang in languages]
        assert "ruby" in lang_names

    def test_detect_csharp_from_csproj(
        self, scanner: StackScanner, csharp_project: Path
    ) -> None:
        result = scanner.scan(csharp_project)
        languages = result.sections["stack"]["languages"]
        lang_names = [lang["name"] for lang in languages]
        assert "csharp" in lang_names

    def test_primary_language_flag(
        self, scanner: StackScanner, node_project: Path
    ) -> None:
        result = scanner.scan(node_project)
        languages = result.sections["stack"]["languages"]
        primary_langs = [lang for lang in languages if lang.get("primary")]
        assert len(primary_langs) == 1

    def test_empty_project_no_languages(
        self, scanner: StackScanner, empty_project: Path
    ) -> None:
        result = scanner.scan(empty_project)
        languages = result.sections["stack"]["languages"]
        assert languages == []


# ---------------------------------------------------------------------------
# Framework detection
# ---------------------------------------------------------------------------


class TestFrameworkDetection:
    """Test framework detection from dependency declarations."""

    def test_detect_nestjs_from_nestjs_core(
        self, scanner: StackScanner, node_project: Path
    ) -> None:
        result = scanner.scan(node_project)
        frameworks = result.sections["stack"]["frameworks"]
        fw_names = [fw["name"] for fw in frameworks]
        assert "nestjs" in fw_names

    def test_detect_express(
        self, scanner: StackScanner, node_project: Path
    ) -> None:
        result = scanner.scan(node_project)
        frameworks = result.sections["stack"]["frameworks"]
        fw_names = [fw["name"] for fw in frameworks]
        assert "express" in fw_names

    def test_detect_react(
        self, scanner: StackScanner, node_project: Path
    ) -> None:
        result = scanner.scan(node_project)
        frameworks = result.sections["stack"]["frameworks"]
        fw_names = [fw["name"] for fw in frameworks]
        assert "react" in fw_names

    def test_detect_fastapi_from_pyproject(
        self, scanner: StackScanner, python_project: Path
    ) -> None:
        result = scanner.scan(python_project)
        frameworks = result.sections["stack"]["frameworks"]
        fw_names = [fw["name"] for fw in frameworks]
        assert "fastapi" in fw_names

    def test_detect_fastapi_from_requirements_txt(
        self, scanner: StackScanner, tmp_path: Path
    ) -> None:
        (tmp_path / "requirements.txt").write_text("fastapi>=0.100.0\nuvicorn\n")
        result = scanner.scan(tmp_path)
        frameworks = result.sections["stack"]["frameworks"]
        fw_names = [fw["name"] for fw in frameworks]
        assert "fastapi" in fw_names

    def test_detect_flask(
        self, scanner: StackScanner, tmp_path: Path
    ) -> None:
        (tmp_path / "requirements.txt").write_text("flask>=2.0\n")
        result = scanner.scan(tmp_path)
        frameworks = result.sections["stack"]["frameworks"]
        fw_names = [fw["name"] for fw in frameworks]
        assert "flask" in fw_names

    def test_detect_django(
        self, scanner: StackScanner, tmp_path: Path
    ) -> None:
        (tmp_path / "requirements.txt").write_text("django>=4.0\n")
        result = scanner.scan(tmp_path)
        frameworks = result.sections["stack"]["frameworks"]
        fw_names = [fw["name"] for fw in frameworks]
        assert "django" in fw_names

    def test_detect_vue(
        self, scanner: StackScanner, tmp_path: Path
    ) -> None:
        pkg = {"name": "vue-app", "dependencies": {"vue": "^3.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        result = scanner.scan(tmp_path)
        frameworks = result.sections["stack"]["frameworks"]
        fw_names = [fw["name"] for fw in frameworks]
        assert "vue" in fw_names

    def test_detect_angular(
        self, scanner: StackScanner, tmp_path: Path
    ) -> None:
        pkg = {"name": "angular-app", "dependencies": {"@angular/core": "^17.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        result = scanner.scan(tmp_path)
        frameworks = result.sections["stack"]["frameworks"]
        fw_names = [fw["name"] for fw in frameworks]
        assert "angular" in fw_names

    def test_detect_nextjs(
        self, scanner: StackScanner, tmp_path: Path
    ) -> None:
        pkg = {"name": "next-app", "dependencies": {"next": "^14.0.0", "react": "^18.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        result = scanner.scan(tmp_path)
        frameworks = result.sections["stack"]["frameworks"]
        fw_names = [fw["name"] for fw in frameworks]
        assert "next.js" in fw_names

    def test_framework_version_extracted(
        self, scanner: StackScanner, node_project: Path
    ) -> None:
        result = scanner.scan(node_project)
        frameworks = result.sections["stack"]["frameworks"]
        express = [fw for fw in frameworks if fw["name"] == "express"][0]
        assert express["version"] is not None
        assert "4.18.0" in express["version"]

    def test_empty_project_no_frameworks(
        self, scanner: StackScanner, empty_project: Path
    ) -> None:
        result = scanner.scan(empty_project)
        frameworks = result.sections["stack"]["frameworks"]
        assert frameworks == []


# ---------------------------------------------------------------------------
# Build tool detection
# ---------------------------------------------------------------------------


class TestBuildToolDetection:
    """Test build tool detection from lock files and manifests."""

    def test_detect_npm_from_package_lock(
        self, scanner: StackScanner, node_project: Path
    ) -> None:
        result = scanner.scan(node_project)
        build_tools = result.sections["stack"]["build_tools"]
        tool_names = [t["name"] for t in build_tools]
        assert "npm" in tool_names

    def test_detect_pnpm_from_lock(
        self, scanner: StackScanner, tmp_path: Path
    ) -> None:
        (tmp_path / "package.json").write_text('{"name": "test"}')
        (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 5.4\n")
        result = scanner.scan(tmp_path)
        build_tools = result.sections["stack"]["build_tools"]
        tool_names = [t["name"] for t in build_tools]
        assert "pnpm" in tool_names

    def test_detect_yarn_from_lock(
        self, scanner: StackScanner, tmp_path: Path
    ) -> None:
        (tmp_path / "package.json").write_text('{"name": "test"}')
        (tmp_path / "yarn.lock").write_text("# yarn lockfile v1\n")
        result = scanner.scan(tmp_path)
        build_tools = result.sections["stack"]["build_tools"]
        tool_names = [t["name"] for t in build_tools]
        assert "yarn" in tool_names

    def test_detect_cargo_from_lock(
        self, scanner: StackScanner, rust_project: Path
    ) -> None:
        result = scanner.scan(rust_project)
        build_tools = result.sections["stack"]["build_tools"]
        tool_names = [t["name"] for t in build_tools]
        assert "cargo" in tool_names

    def test_detect_go_build_tool(
        self, scanner: StackScanner, go_project: Path
    ) -> None:
        result = scanner.scan(go_project)
        build_tools = result.sections["stack"]["build_tools"]
        tool_names = [t["name"] for t in build_tools]
        assert "go" in tool_names

    def test_detect_maven_from_pom(
        self, scanner: StackScanner, java_maven_project: Path
    ) -> None:
        result = scanner.scan(java_maven_project)
        build_tools = result.sections["stack"]["build_tools"]
        tool_names = [t["name"] for t in build_tools]
        assert "maven" in tool_names

    def test_detect_gradle_from_build_gradle(
        self, scanner: StackScanner, java_gradle_project: Path
    ) -> None:
        result = scanner.scan(java_gradle_project)
        build_tools = result.sections["stack"]["build_tools"]
        tool_names = [t["name"] for t in build_tools]
        assert "gradle" in tool_names

    def test_detect_pip_from_requirements(
        self, scanner: StackScanner, tmp_path: Path
    ) -> None:
        (tmp_path / "requirements.txt").write_text("flask>=2.0\n")
        result = scanner.scan(tmp_path)
        build_tools = result.sections["stack"]["build_tools"]
        tool_names = [t["name"] for t in build_tools]
        assert "pip" in tool_names

    def test_empty_project_no_build_tools(
        self, scanner: StackScanner, empty_project: Path
    ) -> None:
        result = scanner.scan(empty_project)
        build_tools = result.sections["stack"]["build_tools"]
        assert build_tools == []


# ---------------------------------------------------------------------------
# Monorepo detection
# ---------------------------------------------------------------------------


class TestMonorepoDetection:
    """Test monorepo detection from workspace configs."""

    def test_detect_monorepo_with_turbo(
        self, scanner: StackScanner, monorepo_project: Path
    ) -> None:
        result = scanner.scan(monorepo_project)
        identity = result.sections["project_identity"]
        assert identity["monorepo"]["detected"] is True
        assert identity["monorepo"]["tool"] == "turborepo"

    def test_detect_monorepo_multiple_languages(
        self, scanner: StackScanner, monorepo_project: Path
    ) -> None:
        result = scanner.scan(monorepo_project)
        languages = result.sections["stack"]["languages"]
        lang_names = [lang["name"] for lang in languages]
        assert "javascript" in lang_names
        assert "python" in lang_names

    def test_detect_pnpm_workspace_monorepo(
        self, scanner: StackScanner, tmp_path: Path
    ) -> None:
        (tmp_path / "package.json").write_text('{"name": "mono", "private": true}')
        (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - 'apps/*'\n")
        result = scanner.scan(tmp_path)
        identity = result.sections["project_identity"]
        assert identity["monorepo"]["detected"] is True

    def test_detect_npm_workspaces_monorepo(
        self, scanner: StackScanner, tmp_path: Path
    ) -> None:
        pkg = {"name": "mono", "private": True, "workspaces": ["packages/*"]}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        result = scanner.scan(tmp_path)
        identity = result.sections["project_identity"]
        assert identity["monorepo"]["detected"] is True

    def test_no_monorepo_single_language(
        self, scanner: StackScanner, node_project: Path
    ) -> None:
        result = scanner.scan(node_project)
        identity = result.sections["project_identity"]
        assert identity["monorepo"]["detected"] is False


# ---------------------------------------------------------------------------
# Project identity
# ---------------------------------------------------------------------------


class TestProjectIdentity:
    """Test project identity extraction."""

    def test_name_from_package_json(
        self, scanner: StackScanner, node_project: Path
    ) -> None:
        result = scanner.scan(node_project)
        identity = result.sections["project_identity"]
        assert identity["name"] == "test-node-project"

    def test_name_from_pyproject_toml(
        self, scanner: StackScanner, python_project: Path
    ) -> None:
        result = scanner.scan(python_project)
        identity = result.sections["project_identity"]
        assert identity["name"] == "test-python-project"

    def test_name_from_go_mod(
        self, scanner: StackScanner, go_project: Path
    ) -> None:
        result = scanner.scan(go_project)
        identity = result.sections["project_identity"]
        assert identity["name"] == "test-go-project"

    def test_name_from_cargo_toml(
        self, scanner: StackScanner, rust_project: Path
    ) -> None:
        result = scanner.scan(rust_project)
        identity = result.sections["project_identity"]
        assert identity["name"] == "test-rust-project"

    def test_fallback_to_directory_name(
        self, scanner: StackScanner, empty_project: Path
    ) -> None:
        result = scanner.scan(empty_project)
        identity = result.sections["project_identity"]
        assert identity["name"] == empty_project.name

    def test_empty_project_type_unknown(
        self, scanner: StackScanner, empty_project: Path
    ) -> None:
        result = scanner.scan(empty_project)
        identity = result.sections["project_identity"]
        assert identity["type"] == "unknown"

    def test_monorepo_type(
        self, scanner: StackScanner, monorepo_project: Path
    ) -> None:
        result = scanner.scan(monorepo_project)
        identity = result.sections["project_identity"]
        assert identity["type"] == "monorepo"

    def test_description_from_package_json(
        self, scanner: StackScanner, node_project: Path
    ) -> None:
        result = scanner.scan(node_project)
        identity = result.sections["project_identity"]
        assert identity["description"] == "A test Node.js project"


# ---------------------------------------------------------------------------
# ScanResult contract
# ---------------------------------------------------------------------------


class TestScanResultContract:
    """Test that scan results follow the expected contract."""

    def test_result_has_source_tags(
        self, scanner: StackScanner, node_project: Path
    ) -> None:
        result = scanner.scan(node_project)
        assert result.sections["project_identity"]["_source"] == "scanner:stack"
        assert result.sections["stack"]["_source"] == "scanner:stack"

    def test_result_has_duration(
        self, scanner: StackScanner, node_project: Path
    ) -> None:
        result = scanner.scan(node_project)
        assert result.duration_ms >= 0

    def test_result_scanner_name(
        self, scanner: StackScanner, node_project: Path
    ) -> None:
        result = scanner.scan(node_project)
        assert result.scanner == "stack"

    def test_empty_project_returns_both_sections(
        self, scanner: StackScanner, empty_project: Path
    ) -> None:
        result = scanner.scan(empty_project)
        assert "project_identity" in result.sections
        assert "stack" in result.sections
