#!/usr/bin/env python3
"""
Tests for artifact_skill_map module.

Validates:
1. expected_skill_for_path() -> recognized extensions resolve to their
   governing skill, case-insensitively, regardless of directory depth.
2. expected_skill_for_path() -> unrecognized extensions, no extension, and
   falsy input all return None (recognition list, not default-deny).
3. expected_skills_for_paths() -> batches expected_skill_for_path() over a
   list, omitting unrecognized paths entirely rather than mapping them to
   None.
4. ARTIFACT_SKILL_RULES structure -- each rule is a well-formed
   ArtifactSkillRule with a non-empty skill name and extension set.

No prior test exercised these two functions directly: skill_injection_verifier
tests only measured artifact_skill_map through the union of
declared_skills + written_paths, never calling expected_skill_for_path or
expected_skills_for_paths in isolation.
"""

import sys
from pathlib import Path

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.agents.artifact_skill_map import (
    ARTIFACT_SKILL_RULES,
    ArtifactSkillRule,
    expected_skill_for_path,
    expected_skills_for_paths,
)


# ============================================================================
# ARTIFACT_SKILL_RULES STRUCTURE
# ============================================================================

class TestArtifactSkillRulesStructure:
    """The rule list itself is well-formed and non-empty."""

    def test_rules_non_empty(self):
        assert len(ARTIFACT_SKILL_RULES) > 0

    def test_every_rule_is_an_artifact_skill_rule(self):
        for rule in ARTIFACT_SKILL_RULES:
            assert isinstance(rule, ArtifactSkillRule)

    def test_every_rule_has_a_non_empty_skill_name(self):
        for rule in ARTIFACT_SKILL_RULES:
            assert isinstance(rule.skill, str)
            assert rule.skill

    def test_every_rule_has_a_non_empty_extension_set(self):
        for rule in ARTIFACT_SKILL_RULES:
            assert isinstance(rule.extensions, frozenset)
            assert len(rule.extensions) > 0

    def test_every_extension_is_lowercase_and_dot_prefixed(self):
        for rule in ARTIFACT_SKILL_RULES:
            for ext in rule.extensions:
                assert ext.startswith("."), f"{ext!r} missing leading dot"
                assert ext == ext.lower(), f"{ext!r} is not lowercase"

    def test_coding_standards_rule_present(self):
        """The initial scope's one registered class -- source code."""
        skills = {rule.skill for rule in ARTIFACT_SKILL_RULES}
        assert "coding-standards" in skills


# ============================================================================
# expected_skill_for_path() -- RECOGNIZED EXTENSIONS
# ============================================================================

class TestExpectedSkillForPathRecognized:
    """Every extension registered under coding-standards resolves to it,
    regardless of directory depth or filename."""

    @pytest.mark.parametrize(
        "file_path",
        [
            "module.py",
            "hooks/modules/agents/thing.py",
            "src/app.js",
            "src/app.mjs",
            "src/app.cjs",
            "src/component.ts",
            "src/component.tsx",
            "src/component.jsx",
            "/absolute/path/to/deep/nested/file.py",
        ],
    )
    def test_recognized_extension_maps_to_coding_standards(self, file_path):
        assert expected_skill_for_path(file_path) == "coding-standards"

    def test_case_insensitive_suffix_match(self):
        """A filesystem may hand back either case for the extension."""
        assert expected_skill_for_path("Module.PY") == "coding-standards"
        assert expected_skill_for_path("App.JS") == "coding-standards"
        assert expected_skill_for_path("Component.TSX") == "coding-standards"

    def test_mixed_case_directory_does_not_affect_match(self):
        assert expected_skill_for_path("Hooks/Modules/Thing.Py") == "coding-standards"


# ============================================================================
# expected_skill_for_path() -- UNRECOGNIZED / EDGE CASES
# ============================================================================

class TestExpectedSkillForPathUnrecognized:
    """A recognition list, not default-deny -- unrecognized input is None,
    never treated as a violation."""

    @pytest.mark.parametrize(
        "file_path",
        [
            "README.md",
            "config.yaml",
            "index.html",
            "data.json",
            "terraform.tf",
            "manifest.yml",
            "archive.tar.gz",  # compound extension -- suffix is only ".gz"
        ],
    )
    def test_unrecognized_extension_returns_none(self, file_path):
        assert expected_skill_for_path(file_path) is None

    def test_no_extension_returns_none(self):
        assert expected_skill_for_path("Makefile") is None
        assert expected_skill_for_path("LICENSE") is None

    def test_dotfile_with_no_real_extension_returns_none(self):
        """A leading dot is the stem for PurePosixPath, not an extension
        marker -- ``.gitignore`` has no ``.suffix``."""
        assert expected_skill_for_path(".gitignore") is None

    def test_empty_string_returns_none(self):
        assert expected_skill_for_path("") is None

    def test_none_input_returns_none(self):
        assert expected_skill_for_path(None) is None

    def test_directory_like_path_with_no_suffix_returns_none(self):
        assert expected_skill_for_path("skills/diagram-builder/assets/") is None


# ============================================================================
# expected_skills_for_paths() -- BATCH BEHAVIOR
# ============================================================================

class TestExpectedSkillsForPaths:
    """Batches expected_skill_for_path() over a sequence, omitting
    unrecognized paths entirely rather than mapping them to None."""

    def test_empty_list_returns_empty_dict(self):
        assert expected_skills_for_paths([]) == {}

    def test_all_recognized_paths_are_mapped(self):
        paths = ["a/engine.js", "b/module.py"]
        result = expected_skills_for_paths(paths)
        assert result == {
            "a/engine.js": "coding-standards",
            "b/module.py": "coding-standards",
        }

    def test_unrecognized_paths_are_omitted_not_mapped_to_none(self):
        paths = ["a/engine.js", "b/README.md", "c/index.html"]
        result = expected_skills_for_paths(paths)
        assert result == {"a/engine.js": "coding-standards"}
        assert "b/README.md" not in result
        assert "c/index.html" not in result

    def test_all_unrecognized_returns_empty_dict(self):
        paths = ["README.md", "config.yaml", "data.json"]
        assert expected_skills_for_paths(paths) == {}

    def test_duplicate_paths_collapse_to_one_key(self):
        paths = ["a/engine.js", "a/engine.js"]
        result = expected_skills_for_paths(paths)
        assert result == {"a/engine.js": "coding-standards"}

    def test_result_is_iterable_without_filtering_by_caller(self):
        """Callers should be able to iterate the result directly -- every
        value is a real skill name, never None."""
        paths = ["a/engine.js", "b/README.md", "c/module.py"]
        result = expected_skills_for_paths(paths)
        for skill in result.values():
            assert skill is not None
            assert isinstance(skill, str) and skill
